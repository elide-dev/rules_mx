"""mx extension for inspecting the resolved graph of a loaded suite."""

import argparse
import json
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


def _dependency_labels(deps, suite, mappings, pending):
    labels = []
    for dep in deps:
        name = dep.qualifiedName()
        if name in mappings:
            labels.extend(mappings[name])
        elif dep.isJavaProject() and dep.suite == suite:
            pending.append(dep)
            labels.append(":" + dep.name)
        else:
            mx.abort(f"{name}: supply --java-dep {name}=LABEL for this dependency")
    return list(dict.fromkeys(labels))


def _mappings(parser, values, flag):
    result = {}
    for mapping in values:
        name, sep, label = mapping.partition("=")
        if not sep or ":" not in name or not label.startswith((":", "//", "@")):
            parser.error(f"expected {flag} SUITE:NAME=LABEL")
        result.setdefault(name, []).append(label)
    return result


def _processor_plugins(project, mappings, java_deps, pending, plugins):
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
        deps = _dependency_labels(dist.deps, project.suite, java_deps, pending)
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
    parser.add_argument("projects", nargs="+")
    options = parser.parse_args(args)
    if options.whole_project and not options.gazelle:
        parser.error("--whole-project requires --gazelle")
    mappings = _mappings(parser, options.processor_plugin, "--processor-plugin")
    java_deps = _mappings(parser, options.java_dep, "--java-dep")
    roots = [mx.project(name) for name in options.projects]
    suite = roots[0].suite
    pending = list(roots)
    rules = {}
    plugins = {}
    graph = {}
    processed = False
    has_resources = False
    while pending:
        project = pending.pop()
        if project.name in rules:
            continue
        if type(project).__name__ != "JavaProject" or project.suite != suite:
            mx.abort(f"{project.name}: requires a separate Bazel dependency mapping")
        unsupported = sorted(set(project._orig_attrs) - _PLAIN_JAVA)
        if unsupported:
            mx.abort(
                f"{project.name}: unsupported mx attributes: {', '.join(unsupported)}"
            )
        processors = _processor_plugins(project, mappings, java_deps, pending, plugins)
        sources = []
        resources = []
        resource_roots = set()
        for directory in map(Path, project.source_dirs()):
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
        deps = _dependency_labels(project.deps, suite, java_deps, pending)
        release = (
            project.javaCompliance.value if options.release is None else options.release
        )
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
            coarse = bool(
                processors or resources or test or project.name in options.whole_project
            )
            record = {
                "name": project.name,
                "options": attrs.get("javac_opts", attrs.get("javacopts")),
                "release": release,
                "deps": deps,
                "roots": sorted(
                    Path(d).relative_to(suite.dir).as_posix()
                    for d in project.source_dirs()
                ),
                "packages": sorted(project.defined_java_packages()),
                "dirs": sorted({str(Path(src).parent) for src in sources}),
            }
            if coarse:
                record["sources"] = sorted(sources)
                if resources:
                    record["resources"] = sorted(resources)
                    record["resource_root"] = resource_root
                if processors:
                    record["processors"] = processors
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
    if rules.keys() & plugins.keys():
        mx.abort("A generated plugin target conflicts with a project name")
    if options.gazelle:
        missing = set(options.whole_project) - graph.keys()
        if missing:
            mx.abort(f"Unknown whole project in selected closure: {sorted(missing)}")
        print(
            json.dumps(
                {
                    "version": 1,
                    "projects": [graph[n] for n in sorted(graph)],
                    "plugins": [plugins[n] for n in sorted(plugins)],
                },
                indent=2,
                sort_keys=True,
            )
        )
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
