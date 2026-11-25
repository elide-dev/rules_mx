"""mx extension for inspecting the resolved graph of a loaded suite."""

import argparse
import json
import re
import textwrap
from pathlib import Path

import mx


@mx.optional_suite_context
@mx.command(
    "mx", "bazel-projects", "[project ...]", props={"keepUnsatisfiedDependencies": True}
)
def bazel_projects(args):
    """Print resolved project inputs with portable suite-relative source roots."""
    if args:
        projects = [mx.project(name) for name in args]
    elif mx.primary_suite() is None:
        projects = mx.suite("mx").projects
    else:
        projects = mx.projects(opt_limit_to_suite=True)
    result = []
    for project in sorted(projects, key=lambda p: (p.suite.name, p.name)):
        root = Path(project.suite.dir)
        record = {
            "suite": project.suite.name,
            "name": project.name,
            "type": type(project).__name__,
            "path": Path(project.dir).relative_to(root).as_posix(),
            "dependencies": sorted(d.qualifiedName() for d in project.deps),
        }
        if project.isJavaProject():
            record.update(
                source_roots=sorted(
                    Path(s).relative_to(root).as_posix() for s in project.source_dirs()
                ),
                compliance=repr(project.javaCompliance),
                processors=sorted(
                    d.qualifiedName() for d in project.annotation_processors()
                ),
            )
        result.append(record)
    print(json.dumps({"version": 1, "projects": result}, indent=2, sort_keys=True))


def _starlark_value(value):
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, list) and len(value) > 1:
        return (
            "[\n"
            + "".join(f"        {json.dumps(item)},\n" for item in value)
            + "    ]"
        )
    return json.dumps(value)


# Only lower metadata for which this exporter has an explicit interpretation.
# mx retains this dictionary before it consumes fields during dependency resolution.
_PLAIN_JAVA = {
    "subDir",
    "dir",
    "sourceDirs",
    "testProject",
    "annotationProcessors",
    "requires",
    "javaCompliance",
    "dependencies",
    "checkstyle",
    "checkstyleVersion",
    "workingSets",
    "checkPackagePrefix",
    "graalCompilerSourceEdition",
    "license",
}


def _rule(kind, attrs, extra=""):
    return (
        kind
        + "(\n"
        + "".join(
            f"    {key} = {_starlark_value(value)},\n" for key, value in attrs.items()
        )
        + extra
        + ")"
    )


def _dependency_labels(deps, suite, mappings, pending, classpath_dists=False):
    labels = []
    for dep in deps:
        name = dep.qualifiedName()
        if name in mappings:
            labels.extend(mappings[name])
        elif dep.suite == suite and (
            dep.isJavaProject()
            or (
                classpath_dists
                and (
                    dep.isJARDistribution()
                    or type(dep).__name__ == "LayoutDirDistribution"
                )
            )
        ):
            pending.append(dep)
            labels.append(":" + dep.name)
        else:
            mx.abort(f"{name}: supply --java-dep {name}=LABEL for this dependency")
    return list(dict.fromkeys(labels))


def _classpath_distribution(dist, suite, mappings, pending):
    if (
        type(dist).__name__ != "JARDistribution"
        or not dist.deps
        or dist.archiveparticipants
        or dist.is_stripped()
        or dist.use_module_path()
        or dist.excludedLibs
        or dist.optionalDependencies
        or dist.buildDependencies
        or dist.overlaps
        or dist.manifestEntries
        or getattr(dist, "javaProperties", None)
        or getattr(dist, "requiresConcealed", None)
    ):
        mx.abort(f"{dist.name}: distribution requires an explicit --java-dep mapping")
    return {
        "name": dist.name,
        "deps": _dependency_labels(dist.deps, suite, mappings, pending, True),
        "testonly": dist.is_test_distribution(),
    }


def _module_directives(dist, members, external, archives):
    info = dist.moduleInfo
    if set(info) - {"name", "requires", "exports", "opens", "uses"}:
        mx.abort(f"{dist.name}: unsupported moduleInfo fields")
    # mx adds observable distribution modules and preserves transitive reads
    # from their declared module descriptors. Explicit requires take precedence.
    requires = {}
    seen = set()

    def visit(dep):
        if dep in seen:
            return
        seen.add(dep)
        if dep.name not in archives or not getattr(dep, "moduleInfo", None):
            mx.abort(
                f"{dist.name}: module path needs an explicit --archive-dist {dep.name}"
            )
        for child in dep.deps:
            if child.isJARDistribution():
                visit(child)

    for dep in external:
        visit(dep)
        requires[dep.moduleInfo["name"]] = dep.moduleInfo["name"]
    observable = {d.moduleInfo["name"] for d in seen}
    for dep in seen:
        for req in dep.moduleInfo.get("requires", []):
            if req.split()[-1] in observable and "transitive" in req.split()[:-1]:
                requires[req.split()[-1]] = req
    for project in members:
        for req in getattr(project, "requires", []):
            requires.setdefault(req, req)
    for req in info.get("requires", []):
        requires[req.split()[-1]] = req
    return sorted(
        ["requires " + req for req in requires.values()]
        + [
            kind + " " + " ".join(entry.split())
            for kind in ("exports", "uses", "opens")
            for entry in info.get(kind, [])
        ]
    )


def _jar_archive(dist, suite, mappings, pending, release, archives):
    participants = {
        (type(p).__module__, type(p).__name__) for p in dist.archiveparticipants
    }
    bridge = ("mx_sdk", "NativeBridgeArchiveParticipant")
    if (
        participants - {bridge}
        or type(dist).__name__ != "JARDistribution"
        or dist.overlaps
        or dist.is_stripped()
        or dist.buildDependencies
        or dist.optionalDependencies
        or dist.manifestEntries
        or getattr(dist, "javaProperties", None)
        or dist.module_info_compilation_participants
    ):
        mx.abort(f"{dist.name}: unsupported JAR archive configuration: {participants}")
    members = dist.archived_deps()
    if any(
        not (
            d.isJavaProject()
            or d.isLibrary()
            or type(d).__name__ == "LayoutDirDistribution"
        )
        for d in members
    ):
        mx.abort(f"{dist.name}: unsupported archive member")
    external = [d for d in dist.deps if d not in members] + dist.excludedLibs
    base = [d for d in members if not getattr(d, "multiReleaseJarVersion", None)]
    overlays = {
        ":" + d.name: str(d.multiReleaseJarVersion) for d in members if d not in base
    }
    pending.extend(d for d in members if d not in base)
    result = {
        "name": dist.name,
        "overlays": overlays,
        "contents": _dependency_labels(base, suite, mappings, pending, True),
        "deps": _dependency_labels(external, suite, mappings, pending, True),
        "testonly": dist.is_test_distribution(),
        "jni_services": bridge in participants,
        "release": release,
    }
    concealed = getattr(dist, "requiresConcealed", {})
    if concealed:
        result["add_exports"] = sorted(
            f"{mod}/{pkg}" for mod, packages in concealed.items() for pkg in packages
        )
    if getattr(dist, "moduleInfo", None):
        result["module"] = dist.moduleInfo["name"]
        result["directives"] = _module_directives(dist, members, external, archives)
    if dist.maven:
        manifest = {"Name": dist.maven_artifact_id()}
        version = suite.release_version_from_suite()
        vendor = suite.developer.get("organization")
        for prefix in ("Specification", "Implementation"):
            if version:
                manifest[prefix + "-Version"] = version
            if vendor:
                manifest[prefix + "-Vendor"] = vendor
        result["manifest"] = manifest
    if getattr(dist, "mainClass", None):
        result.setdefault("manifest", {})["Main-Class"] = dist.mainClass
    return result


def _resource_path(path):
    return path and all(
        part not in (".", "..") and re.fullmatch(r"[a-zA-Z0-9._+-]+", part)
        for part in path.split("/")
    )


def _resource_layout(dist, pending):
    if (
        dist.isPlatformDependent()
        or dist.deps
        or dist.excludedLibs
        or dist.fileListEntry
        or dist.hashEntry
        or dist.fileListPurpose
        or dist._removed_deps
    ):
        mx.abort(f"{dist.name}: unsupported resource layout")
    literals = {}
    copies = {}
    inputs = set()
    for dest, source in dist._walk_layout():
        if not _resource_path(dest) or dest in copies or dest in literals:
            mx.abort(f"{dist.name}: expected a unique resource file path: {dest!r}")
        kind = source["source_type"]
        keys = set(source) - {"_str_", "source_type"}
        if kind == "string" and keys <= {"value", "ignore_value_subst"}:
            literals[dest] = source["value"]
        elif (
            kind == "dependency"
            and keys == {"dependency", "path", "optional"}
            and not source["optional"]
            and _resource_path(source["path"])
        ):
            dep = mx.dependency(source["dependency"], context=dist)
            if type(dep).__name__ != "LayoutDirDistribution" or dep.suite != dist.suite:
                mx.abort(
                    f"{dist.name}: expected a file from a same-suite resource layout"
                )
            inputs.add(dep)
            copies[dest] = f":{dep.name}.files/{source['path']}"
        else:
            mx.abort(
                f"{dist.name}: unsupported resource layout source {source['_str_']}"
            )
    if not (literals or copies) or inputs != set(dist.buildDependencies):
        mx.abort(f"{dist.name}: expected a nonempty file layout with explicit inputs")
    pending.extend(inputs)
    return {
        "name": dist.name,
        "literals": literals,
        "copies": copies,
        "testonly": dist.is_test_distribution(),
    }


def _mappings(parser, values, flag):
    result = {}
    for mapping in values:
        name, sep, label = mapping.partition("=")
        if not sep or ":" not in name or not label.startswith((":", "//", "@")):
            parser.error(f"expected {flag} SUITE:NAME=LABEL")
        result.setdefault(name, []).append(label)
    return result


def _processor_plugins(project, mappings, java_deps, pending, plugins, classpath_dists):
    labels = []
    for dist in project.annotation_processors():
        name = dist.qualifiedName()
        if name in mappings:
            labels.extend(mappings[name])
            continue
        classes = getattr(dist, "definedAnnotationProcessors", None)
        if (
            not dist.isJARDistribution()
            or dist.suite != project.suite
            or not classes
            or dist.excludedLibs
            or dist.archiveparticipants
            or dist.is_stripped()
        ):
            mx.abort(
                f"{project.name}: supply --processor-plugin {name}=LABEL for this processor path"
            )
        deps = _dependency_labels(
            dist.deps, project.suite, java_deps, pending, classpath_dists
        )
        for processor in classes:
            target = f"mx_plugin.{dist.name}.{processor}"
            plugins[target] = {
                "name": target,
                "processor_class": processor,
                "generates_api": True,
                "deps": deps,
            }
            labels.append(":" + target)
    return list(dict.fromkeys(labels))


@mx.optional_suite_context
@mx.command(
    "mx",
    "bazel-buildfile",
    "project [project ...]",
    props={"keepUnsatisfiedDependencies": True},
)
def bazel_buildfile(args):
    """Emit suite-root BUILD declarations for a plain Java project closure."""
    parser = argparse.ArgumentParser(prog="mx bazel-buildfile")
    output = parser.add_mutually_exclusive_group()
    output.add_argument(
        "--gazelle", action="store_true", help="emit the resolved graph for Gazelle"
    )
    parser.add_argument(
        "--whole-project",
        action="append",
        default=[],
        help="retain one compilation for cyclic Java packages",
    )
    output.add_argument(
        "--macro", action="store_true", help="emit a reusable .bzl macro"
    )
    parser.add_argument(
        "--release", type=int, help="select a Java release accepted by every project"
    )
    parser.add_argument(
        "--processor-plugin", action="append", default=[], metavar="SUITE:NAME=LABEL"
    )
    parser.add_argument(
        "--java-dep", action="append", default=[], metavar="SUITE:NAME=LABEL"
    )
    parser.add_argument(
        "--javac-only-processor",
        action="append",
        default=[],
        metavar="SUITE:DIST",
        help="derive the ABI after javac for processors incompatible with Turbine",
    )
    parser.add_argument(
        "--classpath-dists",
        action="store_true",
        help="expose same-suite source JAR distributions as Java classpaths in Gazelle",
    )
    parser.add_argument(
        "--archive-dist",
        action="append",
        default=[],
        metavar="DIST",
        help="assemble a distribution JAR from its resolved mx archive members",
    )
    parser.add_argument(
        "--javac-opt",
        action="append",
        default=[],
        metavar="PROJECT=OPTION",
        help="append a javac option for one project in the selected closure",
    )
    parser.add_argument(
        "--shaded-input",
        action="append",
        default=[],
        metavar="SUITE:LIB=LABEL",
        help="bind a shaded library to its binary or source archive (repeat for both)",
    )
    parser.add_argument("projects", nargs="+")
    options = parser.parse_args(args)
    project_options = {}
    for spec in options.javac_opt:
        name, sep, value = spec.partition("=")
        if not sep or not name or not value.startswith("-"):
            parser.error("expected --javac-opt PROJECT=OPTION")
        project_options.setdefault(name, []).append(value)
    shaded_inputs = _mappings(parser, options.shaded_input, "--shaded-input")
    archives = set(options.archive_dist)
    if archives and not (options.classpath_dists and options.release):
        parser.error("--archive-dist requires --classpath-dists and --release")
    if options.javac_only_processor and not options.gazelle:
        parser.error("--javac-only-processor requires --gazelle")
    if options.whole_project and not options.gazelle:
        parser.error("--whole-project requires --gazelle")
    if options.classpath_dists and not options.gazelle:
        parser.error("--classpath-dists requires --gazelle")
    mappings = _mappings(parser, options.processor_plugin, "--processor-plugin")
    java_deps = _mappings(parser, options.java_dep, "--java-dep")
    lookup = mx.dependency if options.classpath_dists else mx.project
    roots = [lookup(name) for name in options.projects]
    suite = roots[0].suite
    pending = list(roots)
    rules = {}
    plugins = {}
    graph = {}
    distributions = {}
    layouts = {}
    javac_only_processors = set(options.javac_only_processor)
    used_javac_only = set()
    processed = False
    has_resources = False
    while pending:
        project = pending.pop()
        if (
            project.name in rules
            or project.name in distributions
            or project.name in layouts
        ):
            continue
        if options.classpath_dists and project.suite == suite:
            if project.isJARDistribution():
                if project.name in archives:
                    distributions[project.name] = _jar_archive(
                        project, suite, java_deps, pending, options.release, archives
                    )
                    continue
                distributions[project.name] = _classpath_distribution(
                    project, suite, java_deps, pending
                )
                continue
            if type(project).__name__ == "LayoutDirDistribution":
                layouts[project.name] = _resource_layout(project, pending)
                continue
        shaded = options.gazelle and (
            type(project).__module__,
            type(project).__name__,
        ) == ("mx_sdk_shaded", "ShadedLibraryProject")
        if (
            not shaded and type(project).__name__ != "JavaProject"
        ) or project.suite != suite:
            mx.abort(f"{project.name}: requires a separate Bazel dependency mapping")
        allowed = _PLAIN_JAVA
        # Preserve mx coverage exclusions as graph metadata.
        excluded_from_jacoco = project._orig_attrs.get("jacoco") == "exclude"
        if options.gazelle:
            # SpotBugs is a separate mx analysis pass.
            allowed = allowed | {"spotbugs"}
        if options.gazelle and excluded_from_jacoco:
            allowed = allowed | {"jacoco"}
        if options.gazelle:
            allowed = allowed | {"requiresConcealed"}
        if shaded:
            allowed = allowed | {
                "class",
                "shadedDependencies",
                "shade",
                "description",
                "allowsJavadocWarnings",
                "noMavenJavadoc",
                "javac.lint.overrides",
                "overlayTarget",
                "multiReleaseJarVersion",
                "ignoreSrcGenForOverlayMap",
            }
        unsupported = sorted(set(project._orig_attrs) - allowed)
        if unsupported:
            mx.abort(
                f"{project.name}: unsupported mx attributes: {', '.join(unsupported)}"
            )
        processors = _processor_plugins(
            project, mappings, java_deps, pending, plugins, options.classpath_dists
        )
        sources = []
        resources = []
        resource_roots = set()
        source_dirs = [
            s
            for s in project.source_dirs()
            if not shaded or s != project.source_gen_dir()
        ]
        for directory in map(Path, source_dirs):
            if not directory.is_dir():
                mx.abort(f"{project.name}: missing source root {directory}")
            for source in sorted(directory.rglob("*")):
                if source.is_file() and source.name not in {"BUILD", "BUILD.bazel"}:
                    path = source.relative_to(suite.dir).as_posix()
                    if source.suffix == ".java":
                        sources.append(path)
                    else:
                        resources.append(path)
                        resource_roots.add(directory.relative_to(suite.dir).as_posix())
        if not sources:
            mx.abort(f"{project.name}: no Java sources")
        deps = _dependency_labels(
            project.deps, suite, java_deps, pending, options.classpath_dists
        )
        release = (
            project.javaCompliance.value if options.release is None else options.release
        )
        if shaded:
            release = project.javaCompliance.value
        if release not in project.javaCompliance:
            mx.abort(
                f"{project.name}: release {release} is outside {project.javaCompliance!r}"
            )
        attrs = {
            "name": project.name,
            "srcs": sorted(sources),
            "javac_opts": ["--release", str(release)],
        }
        if project._orig_attrs.get("requires"):
            attrs["javac_opts"].extend(
                ["--add-modules", ",".join(project._orig_attrs["requires"])]
            )
        attrs["javac_opts"].extend(project_options.pop(project.name, []))
        if processors:
            processed = True
            attrs["plugins"] = processors
            attrs["javacopts"] = attrs.pop("javac_opts")
        else:
            attrs["unified_release"] = release
        if len(resource_roots) > 1:
            mx.abort(
                f"{project.name}: resources under multiple roots require separate packaging"
            )
        resource_attr = ""
        if resources:
            has_resources = True
            attrs["resources"] = sorted(resources)
            resource_root = resource_roots.pop()
            root = json.dumps(resource_root)
            mode = ", repo_relative = True" if processors else ""
            resource_attr = (
                f"    resource_strip_prefix = mx_resource_prefix({root}{mode}),\n"
            )
        if deps:
            attrs["deps"] = deps
        if options.gazelle:
            test = project.is_test_project()
            concealed = project._orig_attrs.get("requiresConcealed", {})
            coarse = bool(
                shaded
                or concealed
                or processors
                or resources
                or test
                or project.name in options.whole_project
            )
            record = {
                "name": project.name,
                "options": attrs.get("javac_opts", attrs.get("javacopts")),
                "release": release,
                "deps": deps,
                "roots": sorted(
                    Path(d).relative_to(suite.dir).as_posix() for d in source_dirs
                ),
                "packages": [] if shaded else sorted(project.defined_java_packages()),
                "dirs": sorted({str(Path(src).parent) for src in sources}),
            }
            if concealed:
                record["add_exports"] = [
                    f"{mod}/{pkg}" for mod, pkgs in concealed.items() for pkg in pkgs
                ]
                record["options"] = [
                    "-source",
                    str(release),
                    "-target",
                    str(release),
                    *record["options"][2:],
                ]
            if shaded:
                record["shade"] = json.dumps(project.shade)
                inputs = []
                for lib in project.shaded_deps():
                    labels = shaded_inputs.get(lib.qualifiedName(), [])
                    if len(labels) != 2:
                        mx.abort(
                            f"{project.name}: bind the binary and sources of {lib.qualifiedName()} with --shaded-input"
                        )
                    inputs.extend(labels)
                record["shade_inputs"] = inputs
            if "spotbugs" in project._orig_attrs:
                record["spotbugs"] = project._orig_attrs["spotbugs"]
            if excluded_from_jacoco:
                record["jacoco"] = "exclude"
            if coarse:
                record["sources"] = sorted(sources)
                if resources:
                    record["resources"] = sorted(resources)
                    record["resource_root"] = resource_root
                if processors:
                    record["processors"] = processors
                    matched = javac_only_processors.intersection(
                        d.qualifiedName() for d in project.annotation_processors()
                    )
                    if matched:
                        used_javac_only.update(matched)
                        record["abi_after_javac"] = True
                if test:
                    record["testonly"] = True
                    record["tests"] = sorted(
                        project.find_classes_with_annotations(
                            None,
                            [
                                "@Test",
                                "@org.junit.Test",
                                "@RunWith",
                                "@org.junit.runner.RunWith",
                            ],
                        )
                    )
            graph[project.name] = record
        rules[project.name] = _rule(
            "java_library" if processors else "elide_java_library", attrs, resource_attr
        )
    if project_options:
        mx.abort(f"Unknown javac option project: {sorted(project_options)}")
    if rules.keys() & plugins.keys():
        mx.abort("A generated plugin target conflicts with a project name")
    if options.gazelle:
        if archives - distributions.keys():
            mx.abort(
                f"Unknown archive in selected closure: {sorted(archives - distributions.keys())}"
            )
        unmatched = javac_only_processors - used_javac_only
        if unmatched:
            mx.abort(
                f"Unknown javac-only processor in selected closure: {sorted(unmatched)}"
            )
        missing = set(options.whole_project) - graph.keys()
        if missing:
            mx.abort(f"Unknown whole project in selected closure: {sorted(missing)}")
        result = {
            "version": 1,
            "projects": [graph[n] for n in sorted(graph)],
            "plugins": [plugins[n] for n in sorted(plugins)],
        }
        if distributions:
            result["distributions"] = [distributions[n] for n in sorted(distributions)]
        if layouts:
            result["layouts"] = [layouts[n] for n in sorted(layouts)]
        print(json.dumps(result, indent=2, sort_keys=True))
        return
    rules.update({name: _rule("java_plugin", attrs) for name, attrs in plugins.items()})
    print('load("@rules_mx//mx:java.bzl", "elide_java_library")')
    if processed:
        print(
            'load("@rules_mx//mx:processing.bzl", "java_library"'
            + (', "java_plugin"' if plugins else "")
            + ")"
        )
    if has_resources:
        print('load("@rules_mx//mx:java.bzl", "mx_resource_prefix")')
    print()
    body = "\n\n".join(rules[name] for name in sorted(rules))
    if options.macro:
        print("def mx_java_projects():")
        body = textwrap.indent(body, "    ")
    print(body)
