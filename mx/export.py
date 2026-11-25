"""mx extension for inspecting the resolved graph of a loaded suite."""

import argparse
from functools import cache
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


@cache
def _source_packages(project):
    """Index checked-in Java packages without resolving binary library contents."""
    packages = set()
    for directory in map(Path, project.source_dirs()):
        for source in directory.rglob("*.java"):
            if source.name != "module-info.java" and source.parent != directory:
                packages.add(".".join(source.relative_to(directory).parts[:-1]))
    return frozenset(packages)


def _source_dependencies(graph, suite, mappings, distributions):
    """Promote imported types from the resolved mx classpath to direct Bazel deps."""
    imports = re.compile(r"^\s*import\s+(?:static\s+)?([\w.$*]+)\s*;", re.MULTILINE)
    packages = {}

    def provided(dep):
        if dep not in packages:
            members = dep.archived_deps() if dep.isJARDistribution() else [dep]
            packages[dep] = {
                pkg
                for p in members
                if type(p).__name__ == "JavaProject"
                for pkg in _source_packages(p)
            }
        return packages[dep]

    for name, record in graph.items():
        if not record.get("sources"):
            continue
        owners = {}
        todo = list(mx.project(name).deps)
        visited = set()
        while todo:
            dep = todo.pop()
            if dep in visited:
                continue
            visited.add(dep)
            if dep.isJARDistribution():
                todo.extend(d for d in dep.deps if d.isJARDistribution())
            elif dep.isJavaProject():
                todo.extend(dep.deps)
            else:
                continue
            labels = mappings.get(dep.qualifiedName())
            if dep.suite == suite and (dep.name in graph or dep.name in distributions):
                labels = [":" + dep.name]
            if labels:
                for pkg in provided(dep):
                    owners.setdefault(pkg, set()).update(labels)
        direct = set(record["deps"])
        for src in record["sources"]:
            for qualified in imports.findall(
                (Path(suite.dir) / src).read_text(encoding="utf-8")
            ):
                parts = qualified.split(".")[:-1]
                while parts:
                    pkg = ".".join(parts)
                    if pkg in owners:
                        direct.update(owners[pkg])
                        break
                    parts.pop()
        record["deps"] += sorted(direct.difference(record["deps"]))


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


def _module_directives(dist, members, external, archives, mappings, library_modules):
    info = dist.moduleInfo
    if set(info) - {
        "name",
        "requires",
        "exports",
        "opens",
        "uses",
        "requiresConcealed",
    }:
        mx.abort(f"{dist.name}: unsupported moduleInfo fields")
    # mx adds observable distribution modules and preserves transitive reads
    # from their declared module descriptors. Explicit requires take precedence.
    requires = {}
    seen = set()

    def visit(dep):
        if dep in seen:
            return
        seen.add(dep)
        if (dep.suite == dist.suite and dep.name not in archives) or not getattr(
            dep, "moduleInfo", None
        ):
            mx.abort(
                f"{dist.name}: module path needs an explicit --archive-dist {dep.name}"
            )
        for child in dep.deps:
            if child.isJARDistribution():
                visit(child)

    for dep in external:
        module = getattr(dep, "moduleName", None) or library_modules.get(
            dep.qualifiedName()
        )
        if dep.isLibrary() and module and dep.qualifiedName() in mappings:
            requires[module] = module
        else:
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

    directives = {"requires " + req for req in requires.values()}
    directives.update("uses " + entry for entry in info.get("uses", []))
    directives.update(
        "uses " + entry for p in members for entry in getattr(p, "uses", [])
    )
    for kind in ("exports", "opens"):
        packages = {}
        for entry in info.get(kind, []):
            subject, to, names = " ".join(entry.split()).partition(" to ")
            matches = [subject]
            # Shaded package names are known after source relocation and compilation.
            if subject.endswith("*") and not any(
                getattr(p, "shadedDependencies", None) for p in members
            ):
                matches = sorted(
                    {
                        pkg
                        for p in members
                        if p.isJavaProject()
                        for pkg in _source_packages(p)
                        if pkg.startswith(subject[:-1])
                    }
                )
                if not matches:
                    mx.abort(f"{dist.name}: no packages match {subject}")
            for package in matches:
                if not to:
                    packages[package] = None
                elif packages.setdefault(package, []) is not None:
                    packages[package].extend(n.strip() for n in names.split(","))
        for package, names in packages.items():
            targets = " to " + ", ".join(dict.fromkeys(names)) if names else ""
            directives.add(f"{kind} {package}{targets}")
    return sorted(directives)


def _jar_archive(dist, suite, mappings, pending, release, archives, library_modules):
    participants = {
        (type(p).__module__, type(p).__name__) for p in dist.archiveparticipants
    }
    bridge = ("mx_sdk", "NativeBridgeArchiveParticipant")
    truffle = ("mx_truffle", "TruffleArchiveParticipant")
    graal = ("mx_compiler", "GraalArchiveParticipant")
    jmh = ("mx._impl.mx", "JMHArchiveParticipant")
    if (
        participants - {bridge, truffle, graal, jmh}
        or type(dist).__name__ != "JARDistribution"
        or dist.overlaps
        or dist.is_stripped()
        or dist.buildDependencies
        or any(
            d not in dist.deps
            or ("static " + getattr(d, "moduleInfo", {}).get("name", ""))
            not in getattr(dist, "moduleInfo", {}).get("requires", [])
            for d in dist.optionalDependencies
        )
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
        "truffle_services": truffle in participants,
        "graal_services": graal in participants,
        "jmh_metadata": jmh in participants,
        "release": release,
    }
    if any(
        type(p).__name__ == "GraalArchiveParticipant" and p.isTest
        for p in dist.archiveparticipants
    ):
        result["graal_test_services"] = True
    if pattern := getattr(suite, "snippetsPattern", None):
        result["snippets_pattern"] = pattern
        result["archived_libraries"] = _dependency_labels(
            [d for d in base if d.isLibrary()], suite, mappings, pending, True
        )
    concealed = getattr(dist, "requiresConcealed", {})
    concealed = {
        **concealed,
        **getattr(dist, "moduleInfo", {}).get("requiresConcealed", {}),
    }
    if concealed:
        result["add_exports"] = sorted(
            f"{mod}/{pkg}" for mod, packages in concealed.items() for pkg in packages
        )
    if getattr(dist, "moduleInfo", None):
        result["module"] = dist.moduleInfo["name"]
        result["directives"] = _module_directives(
            dist, members, external, archives, mappings, library_modules
        )
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


def _resource_layout(dist, pending, mappings):
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
            if type(dep).__name__ != "LayoutDirDistribution":
                mx.abort(f"{dist.name}: expected a file from a resource layout")
            inputs.add(dep)
            if dep.suite == dist.suite:
                target = ":" + dep.name
                pending.append(dep)
            else:
                labels = mappings.get(dep.qualifiedName(), [])
                if len(labels) != 1:
                    mx.abort(
                        f"{dist.name}: bind {dep.qualifiedName()} to a migrated resource layout"
                    )
                target = labels[0]
            copies[dest] = f"{target}.files/{source['path']}"
        else:
            mx.abort(
                f"{dist.name}: unsupported resource layout source {source['_str_']}"
            )
    if not (literals or copies) or inputs != set(dist.buildDependencies):
        mx.abort(f"{dist.name}: expected a nonempty file layout with explicit inputs")
    return {
        "name": dist.name,
        "literals": literals,
        "copies": copies,
        "testonly": dist.is_test_distribution(),
    }


def _junit_classes(project):
    candidates = project.find_classes_with_annotations(
        None, ["@Test", "@org.junit.Test", "@RunWith", "@org.junit.runner.RunWith"]
    )
    # mx finds annotated source files, including abstract parameterized test bases.
    # Ignore comments and literals before inspecting the class declaration.
    non_code = re.compile(
        r'''//[^\n]*|/\*.*?\*/|""".*?"""|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*[']''',
        re.DOTALL,
    )
    abstract = re.compile(
        r"\babstract\s+(?:(?:public|protected|private|static|sealed|non-sealed|strictfp)\s+)*class\s+(\w+)"
    )
    classes = []
    for classname, (source, _) in candidates.items():
        declarations = abstract.findall(non_code.sub(" ", Path(source).read_text()))
        if classname.rsplit(".", 1)[-1] not in declarations:
            classes.append(classname)
    return sorted(classes)


def _mappings(parser, values, flag):
    result = {}
    for mapping in values:
        name, sep, label = mapping.partition("=")
        if not sep or ":" not in name or not label.startswith((":", "//", "@")):
            parser.error(f"expected {flag} SUITE:NAME=LABEL")
        result.setdefault(name, []).append(label)
    return result


def _processor_plugins(
    project, mappings, java_deps, pending, plugins, classpath_dists, archives
):
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
            or (dist.archiveparticipants and dist.name not in archives)
            or dist.is_stripped()
        ):
            mx.abort(
                f"{project.name}: supply --processor-plugin {name}=LABEL for this processor path"
            )
        if dist.archiveparticipants and dist.name in archives:
            pending.append(dist)
            deps = [":" + dist.name]
        else:
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
        "--library-module",
        action="append",
        default=[],
        metavar="SUITE:LIB=MODULE",
        help="JPMS name of a mapped automatic module",
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
    parser.add_argument(
        "--infer-source-deps",
        action="store_true",
        help="add direct imports of projects and distributions on the resolved mx classpath",
    )
    parser.add_argument(
        "--generated-dep",
        action="append",
        default=[],
        metavar="SUITE:PROJECT=LABEL",
        help="direct Java dependency referenced by generated sources",
    )
    parser.add_argument(
        "--test-runtime",
        action="append",
        default=[],
        metavar="SUITE:PROJECT=LABEL",
        help="discover JUnit classes and run them against these packaged runtime dependencies",
    )
    parser.add_argument(
        "--test-path",
        action="append",
        default=[],
        metavar="SUITE:DIST=LABEL",
        help="bind mx test Java properties using <path:DIST> to Bazel data",
    )
    parser.add_argument(
        "--test-jvm-flag",
        action="append",
        default=[],
        metavar="SUITE:PROJECT=FLAG",
    )
    parser.add_argument(
        "--test-launcher",
        action="append",
        default=[],
        metavar="SUITE:PROJECT=LABEL",
        help="use a shared executable that reads MX_TEST_CLASS",
    )
    parser.add_argument(
        "--manual-test-project",
        action="append",
        default=[],
        help="tag generated JUnit classes for explicit invocation",
    )
    parser.add_argument(
        "--project-release",
        action="append",
        default=[],
        metavar="PROJECT=RELEASE",
        help="override the selected Java release for one project",
    )
    parser.add_argument("projects", nargs="+")
    options = parser.parse_args(args)
    library_modules = {}
    for spec in options.library_module:
        name, sep, module = spec.partition("=")
        if (
            not sep
            or ":" not in name
            or not re.fullmatch(r"[A-Za-z][\w]*(?:\.[A-Za-z][\w]*)*", module)
        ):
            parser.error("expected --library-module SUITE:LIB=MODULE")
        if name in library_modules:
            parser.error(f"duplicate --library-module mapping for {name}")
        library_modules[name] = module
    test_launchers = _mappings(parser, options.test_launcher, "--test-launcher")
    test_runtimes = _mappings(parser, options.test_runtime, "--test-runtime")
    test_paths = _mappings(parser, options.test_path, "--test-path")
    used_test_paths = set()
    test_flags = {}
    for spec in options.test_jvm_flag:
        name, sep, value = spec.partition("=")
        if not sep or ":" not in name or not value.startswith("-"):
            parser.error("expected --test-jvm-flag SUITE:PROJECT=FLAG")
        test_flags.setdefault(name, []).append(value)
    manual_tests = set(options.manual_test_project)
    project_releases = {}
    for spec in options.project_release:
        name, sep, value = spec.partition("=")
        if (
            not name
            or not sep
            or not value.isdecimal()
            or int(value) < 1
            or name in project_releases
        ):
            parser.error("expected a unique --project-release PROJECT=RELEASE")
        project_releases[name] = int(value)
    if (
        test_runtimes or test_launchers or test_flags or manual_tests
    ) and not options.gazelle:
        parser.error("test configuration requires --gazelle")
    project_options = {}
    for spec in options.javac_opt:
        name, sep, value = spec.partition("=")
        if not sep or not name or not value.startswith("-"):
            parser.error("expected --javac-opt PROJECT=OPTION")
        project_options.setdefault(name, []).append(value)
    shaded_inputs = _mappings(parser, options.shaded_input, "--shaded-input")
    if options.infer_source_deps and not options.gazelle:
        parser.error("--infer-source-deps requires --gazelle")
    generated_deps = _mappings(parser, options.generated_dep, "--generated-dep")
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
                        project,
                        suite,
                        java_deps,
                        pending,
                        options.release,
                        archives,
                        library_modules,
                    )
                    continue
                distributions[project.name] = _classpath_distribution(
                    project, suite, java_deps, pending
                )
                continue
            if type(project).__name__ == "LayoutDirDistribution":
                layouts[project.name] = _resource_layout(project, pending, java_deps)
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
            allowed = allowed | {"spotbugs", "spotbugsIgnoresGenerated", "javadocType"}
            if getattr(project, "multiReleaseJarVersion", None):
                allowed = allowed | {"multiReleaseJarVersion", "overlayTarget"}
        if options.gazelle and excluded_from_jacoco:
            allowed = allowed | {"jacoco"}
        if options.gazelle:
            allowed = allowed | {"requiresConcealed", "generatedDependencies", "uses"}
            if project._orig_attrs.get("jacoco") == "include":
                allowed = allowed | {"jacoco", "jacocoExcludePackages"}
            if project._orig_attrs.get("javac.lint.overrides") == "none":
                allowed = allowed | {"javac.lint.overrides"}
        jni_headers = options.gazelle and project._orig_attrs.get("jniHeaders") is True
        if jni_headers:
            allowed = allowed | {"jniHeaders"}
        # Solaris is outside the native target platforms served by this migration.
        if options.gazelle and project._orig_attrs.get("os_arch") == {
            "solaris": {"<others>": {"ignore": "temporarily disabled"}},
            "<others>": {"<others>": {"ignore": False}},
        }:
            allowed = allowed | {"os_arch"}
        property_flags = []
        property_data = []
        if (
            options.gazelle
            and project.is_test_project()
            and project._orig_attrs.get("javaProperties")
        ):
            for key, value in project._orig_attrs["javaProperties"].items():
                match = re.fullmatch(r"<path:([^>]+)>", value)
                dep = mx.dependency(match[1], context=project) if match else None
                labels = test_paths.get(dep.qualifiedName(), []) if dep else []
                if len(labels) != 1:
                    mx.abort(f"{project.name}: bind {key}={value} using --test-path")
                used_test_paths.add(dep.qualifiedName())
                property_data.append(labels[0])
                property_flags.append(f"-D{key}=$(rootpath {labels[0]})")
            allowed = allowed | {"javaProperties"}
        if shaded:
            allowed = allowed | {
                "class",
                "spotbugsIgnoresGenerated",
                "forceJavac",
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
        force_javac = project._orig_attrs.get("forceJavac") is True
        if options.gazelle and force_javac:
            allowed = allowed | {"forceJavac"}
        if project._orig_attrs.get("javac.lint.overrides") == "-incubating":
            allowed = allowed | {"javac.lint.overrides"}
        unsupported = sorted(set(project._orig_attrs) - allowed)
        if unsupported:
            mx.abort(
                f"{project.name}: unsupported mx attributes: {', '.join(unsupported)}"
            )
        processors = _processor_plugins(
            project,
            mappings,
            java_deps,
            pending,
            plugins,
            options.classpath_dists,
            archives,
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
            if shaded and not directory.exists():
                continue  # The suite may contain only generated sources.
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
        if not sources and not shaded:
            mx.abort(f"{project.name}: no Java sources")
        deps = _dependency_labels(
            project.deps, suite, java_deps, pending, options.classpath_dists
        )
        deps.extend(generated_deps.pop(project.qualifiedName(), []))
        release = (
            project.javaCompliance.value if options.release is None else options.release
        )
        if shaded or getattr(project, "multiReleaseJarVersion", None):
            release = project.javaCompliance.value
        release = project_releases.pop(project.name, release)
        if release not in project.javaCompliance:
            mx.abort(
                f"{project.name}: release {release} is outside {project.javaCompliance!r}"
            )
        attrs = {
            "name": project.name,
            "srcs": sorted(sources),
            "javac_opts": ["--release", str(release)],
        }
        if project.javaCompliance.value >= 8:
            attrs["javac_opts"].append("-parameters")
        if project._orig_attrs.get("requires"):
            attrs["javac_opts"].extend(
                ["--add-modules", ",".join(project._orig_attrs["requires"])]
            )
        if lint := project._orig_attrs.get("javac.lint.overrides"):
            if lint in ("none", "-incubating"):
                attrs["javac_opts"].append("-Xlint:" + lint)
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
            test_runtime = test_runtimes.pop(project.qualifiedName(), [])
            launchers = test_launchers.pop(project.qualifiedName(), [])
            discover_tests = test or bool(test_runtime or launchers)
            concealed = project._orig_attrs.get("requiresConcealed", {})
            coarse = bool(
                shaded
                or concealed
                or jni_headers
                or force_javac
                or processors
                or resources
                or discover_tests
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
                "packages": [] if shaded else sorted(_source_packages(project)),
                "dirs": sorted({str(Path(src).parent) for src in sources}),
            }
            if shaded and not record["dirs"]:
                record["dirs"] = record["roots"]
            if "jdk.internal.vm.ci" in concealed:
                record["jvmci"] = True
            if jni_headers:
                record["jni_headers"] = True
            if force_javac:
                record["force_javac"] = True
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
            if jacoco := project._orig_attrs.get("jacoco"):
                record["jacoco"] = jacoco
            if exclusions := project._orig_attrs.get("jacocoExcludePackages"):
                record["jacoco_exclude_packages"] = exclusions
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
                if discover_tests:
                    record["tests"] = _junit_classes(project)
                    if launchers:
                        if (
                            len(launchers) != 1
                            or test_runtime
                            or property_data
                            or project.qualifiedName() in test_flags
                        ):
                            mx.abort(
                                f"{project.name}: --test-launcher supplies the entire test runtime"
                            )
                        record["test_launcher"] = launchers[0]
                    if project.name in manual_tests:
                        manual_tests.remove(project.name)
                        record["manual_tests"] = True
                    if test_runtime:
                        record["test_runtime"] = test_runtime
                    if property_data:
                        record["test_data"] = sorted(set(property_data))
                    if flags := property_flags + test_flags.pop(
                        project.qualifiedName(), []
                    ):
                        record["test_jvm_flags"] = flags
            graph[project.name] = record
        rules[project.name] = _rule(
            "java_library" if processors else "elide_java_library", attrs, resource_attr
        )
    if project_releases or manual_tests:
        mx.abort(
            f"Unused project configuration: {sorted(project_releases.keys() | manual_tests)}"
        )
    if unused_paths := test_paths.keys() - used_test_paths:
        mx.abort(f"Unused test path mappings: {sorted(unused_paths)}")
    if test_launchers or test_runtimes or test_flags:
        mx.abort(
            f"Unused test configuration: {sorted(test_runtimes.keys() | test_flags.keys() | test_launchers.keys())}"
        )
    if generated_deps:
        mx.abort(f"Unknown generated dependency project: {sorted(generated_deps)}")
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
        if options.infer_source_deps:
            _source_dependencies(graph, suite, java_deps, distributions)
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
