"""mx extension for inspecting the resolved graph of a loaded suite."""

import argparse
import fnmatch
from functools import cache
import json
import os
import re
import shlex
import textwrap
from pathlib import Path
from types import SimpleNamespace

import mx
from mx._impl.mx import glob_match
from mx._impl.mx_maven import _genPom


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


def _graph_path(path, suite, owners):
    path = Path(os.path.normpath(path))
    for candidate in (path, path.resolve()):
        for owner_root, owner in owners:
            if candidate.is_relative_to(owner_root):
                return {**owner, "path": candidate.relative_to(owner_root).as_posix()}
    mx.abort(f"{suite.name}: graph input has no suite or packed-resource owner: {path}")


@mx.optional_suite_context
@mx.command(
    "mx", "bazel-graph", "[--imports]", props={"keepUnsatisfiedDependencies": True}
)
def bazel_graph(args):
    """Describe the resolved suite graph without resolving artifact files."""
    parser = argparse.ArgumentParser(prog="mx bazel-graph")
    parser.add_argument(
        "--imports", action="store_true", help="include loaded imported suites"
    )
    parser.add_argument(
        "--cmake-tests",
        action="store_true",
        help="include source cases and variants for in-tree Sulong CMake test projects",
    )
    options = parser.parse_args(args)
    primary = mx.primary_suite() or mx.suite("mx")
    loaded = mx.suites(include_mx=True)
    suites = loaded if options.imports else [primary]
    suite_roots = [
        (root, {"suite": s.name})
        for s in loaded
        for root in {Path(s.dir), Path(s.dir).resolve()}
    ]
    result = {}
    for suite in sorted(suites, key=lambda item: item.name):
        nodes = {}
        for kind, dependencies in (
            ("project", suite.projects),
            ("distribution", suite.dists),
            ("library", suite.libs + suite.jreLibs + suite.jdkLibs),
        ):
            for dep in dependencies:
                edges = {}

                def edge(_, target, relation):
                    edges.setdefault(relation.kind, []).append(target.qualifiedName())

                dep.walk_deps(
                    preVisit=lambda _, relation: relation is None,
                    visitEdge=edge,
                    ignoredEdges=[],
                )
                node = {
                    "kind": kind,
                    "type": type(dep).__name__,
                    "edges": edges,
                    "platform_dependent": dep.isPlatformDependent(),
                }
                if kind == "project":
                    owners = [
                        (
                            root,
                            {"library": lib.qualifiedName()},
                        )
                        for lib in getattr(dep, "buildDependencies", []) + dep.deps
                        if lib.isPackedResourceLibrary() and lib.extract_path
                        for path in [Path(lib.get_path(resolve=False))]
                        for root in {path, path.resolve()}
                    ] + suite_roots
                    owners.sort(key=lambda item: len(item[0].parts), reverse=True)
                    directory = _graph_path(dep.dir, suite, owners)
                    if directory.get("suite") == suite.name:
                        node["path"] = directory["path"]
                    else:
                        node["directory_input"] = directory
                    inputs = [_graph_path(p, suite, owners) for p in dep.source_dirs()]
                    if all(p.get("suite") == suite.name for p in inputs):
                        node["source_roots"] = [p["path"] for p in inputs]
                    else:
                        node["source_inputs"] = inputs
                if options.cmake_tests and _in_tree_sulong_tests(dep):
                    node["cmake_tests"] = _cmake_cases(dep)
                if dep.isJavaProject():
                    node["compliance"] = repr(dep.javaCompliance)
                    node["testonly"] = dep.is_test_project()
                if dep.isDistribution():
                    node["testonly"] = dep.is_test_distribution()
                    if dep.isJARDistribution():
                        node["archive_members"] = [
                            member.qualifiedName() for member in dep.archived_deps()
                        ]
                    if hasattr(dep, "layout"):
                        node["layout"] = dep.layout
                nodes[dep.qualifiedName()] = node
        result[suite.name] = nodes
    print(
        json.dumps(
            {"version": 1, "primary_suite": primary.name, "suites": result},
            indent=2,
            sort_keys=True,
        )
    )


def _in_tree_sulong_tests(dep):
    # ExternalCMakeTestSuite.getTests() materializes packed libraries.
    return (type(dep).__module__, type(dep).__name__) == (
        "mx_sulong_suite_constituents",
        "SulongCMakeTestSuite",
    )


def _cmake_cases(project):
    return {
        "cases": sorted(Path(case).as_posix() for case in project.getTests()),
        "variants": list(project.getVariants()),
        "build_reference": project.buildRef,
        "shared_object": project.buildSharedObject,
        "variant_config": project.cmake_config_variant,
    }


def _bitcode_suite(project, linker):
    if not linker:
        raise _UnsupportedLowering("Sulong CMake bitcode requires --sulong-llvm-link")
    matrix = _cmake_cases(project)
    declaration = project._orig_attrs
    if (
        matrix["variants"] != ["bitcode-O0"]
        or matrix["build_reference"]
        or matrix["shared_object"]
        or matrix["variant_config"]
        or not matrix["cases"]
        or any(not case.endswith(".ll") for case in matrix["cases"])
        or declaration.keys()
        - {
            "subDir",
            "class",
            "variants",
            "fileExts",
            "buildRef",
            "buildSharedObject",
            "bundledLLVMOnly",
            "dependencies",
            "buildDependencies",
            "testProject",
            "defaultBuild",
        }
    ):
        raise _UnsupportedLowering(
            "Sulong CMake requires additional compile, reference or link steps"
        )
    root = Path(project.source_dirs()[0])
    recipe = [
        line.strip()
        for line in (root / "CMakeLists.txt").read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if recipe != [
        "cmake_minimum_required(VERSION 3.15)",
        "project(${SULONG_PROJECT_NAME} LANGUAGES NONE)",
        "include(SulongTestSuite)",
    ] or any(root.rglob("TestOptions.cmake")):
        raise _UnsupportedLowering(
            "Sulong CMake suite has custom build steps or per-case options"
        )
    return {
        "name": project.name,
        "root": root.relative_to(project.suite.dir).as_posix(),
        "cases": matrix["cases"],
        "linker": linker,
        "testonly": project.is_test_project(),
    }


def _cmake_fixture_suite(project, sdk):
    matrix = _cmake_cases(project)
    declaration = project._orig_attrs
    if (
        matrix["shared_object"]
        or matrix["variant_config"]
        or not matrix["cases"]
        or any(
            not re.fullmatch(r"bitcode-O(?:[0-3s])(?:-MEM2REG)?", v)
            for v in matrix["variants"]
        )
    ):
        raise _UnsupportedLowering(
            "Sulong fixture requires its bootstrap toolchain or a custom variant"
        )
    # Configure for the target independently of the machine running Gazelle.
    settings = dict(declaration.get("cmakeConfig", {}))
    os_config = declaration.get("os", {})
    linux = os_config.get("linux", os_config.get("<others>", {}))
    if (
        set(linux) - {"cmakeConfig"}
        or "arch" in declaration
        or "os_arch" in declaration
    ):
        raise _UnsupportedLowering("Sulong fixture needs platform-dependent inputs")
    deps = set(declaration.get("buildDependencies", []))
    if deps - {"SULONG_HOME", "LINUX_AMD64_SUPPORT"}:
        raise _UnsupportedLowering("Sulong fixture needs another build-time dependency")
    settings.update(linux.get("cmakeConfig", {}))
    if settings.keys() - {
        "CMAKE_C_FLAGS",
        "CMAKE_CXX_FLAGS",
        "CMAKE_C_LINK_FLAGS",
        "CMAKE_EXE_LINKER_FLAGS",
    } or any("<" in v for v in settings.values()):
        raise _UnsupportedLowering("Sulong CMake flags require unresolved mx inputs")
    languages = sorted(project._get_languages())
    if set(languages) - {"C", "CXX", "LL"}:
        raise _UnsupportedLowering("Sulong CMake needs another input language")
    root = Path(project.source_dirs()[0]).relative_to(project.suite.dir)
    modules = mx.project("com.oracle.truffle.llvm.tests.cmake")
    return {
        "name": project.name,
        "root": root.as_posix(),
        "sdk": sdk,
        "config": settings,
        "modules": Path(modules.dir).relative_to(project.suite.dir).as_posix(),
        "cases": matrix["cases"],
        "languages": languages,
        "variants": matrix["variants"]
        + (["ref.out"] if matrix["build_reference"] else []),
        "testonly": project.is_test_project(),
    }


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


def _write_bindings(path, graph):
    projects = {p["name"]: p.get("packages", []) for p in graph["projects"]}
    java = dict(projects)
    artifacts = []
    processors = {}
    emitted = {p["name"] for p in graph["plugins"]}
    suite = mx.primary_suite() or mx.suite("mx")
    for record in graph.get("distributions", []):
        dist = mx.dependency(f"{suite.name}:{record['name']}")
        if dist.isPOMDistribution():
            java[dist.name] = []
            continue
        if not dist.isJARDistribution():
            continue
        artifacts.append(dist.name)
        java[dist.name] = sorted(
            {
                pkg
                for member in dist.archived_deps()
                if type(member).__name__ == "JavaProject"
                for pkg in _source_packages(member)
            }
        )
    for dist in suite.dists:
        labels = [
            f"mx_plugin.{dist.name}.{cls}"
            for cls in getattr(dist, "definedAnnotationProcessors", [])
        ]
        if labels and emitted.issuperset(labels):
            processors[dist.name] = labels
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "suite": suite.name,
                "java": java,
                "processors": processors,
                "artifacts": sorted(artifacts),
                **({"source_archives": sorted(graph["source_exports"])} if graph.get("source_exports") else {}),
            },
            sort_keys=True,
            indent=2,
        )
        + "\n"
    )


def _read_bindings(parser, options):
    result = {"java": {}, "plugins": {}, "packages": {}, "artifacts": {}, "source_archives": {}}
    suites = set()
    for spec in options.bindings:
        package, sep, filename = spec.partition("=")
        if not sep or not re.fullmatch(
            r"(?:@[A-Za-z0-9_.+-]+)?//[A-Za-z0-9_./+-]*", package
        ):
            parser.error("expected --bindings //PACKAGE=FILE")
        index = json.loads(Path(filename).read_text())
        suite = index["suite"]
        if index["version"] != 1 or suite in suites:
            parser.error(f"unsupported or duplicate bindings for {suite}")
        suites.add(suite)
        for name, packages in index["java"].items():
            qualified = f"{suite}:{name}"
            result["java"][qualified] = f"{package}:{name}"
            result["packages"][qualified] = packages
        for name in index.get("source_archives", []):
            result["source_archives"][f"{suite}:{name}"] = f"{package}:{name}.sources"
        for name in index.get("artifacts", []):
            result["artifacts"][f"{suite}:{name}"] = [f"{package}:{name}"]
        for name, plugins in index["processors"].items():
            result["plugins"][f"{suite}:{name}"] = [f"{package}:{p}" for p in plugins]
    return result


class _UnsupportedLowering(Exception):
    """A resolved mx feature needs a Bazel implementation or binding."""


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
                    or dep.isPOMDistribution()
                    or type(dep).__name__ == "LayoutDirDistribution"
                )
            )
        ):
            pending.append(dep)
            labels.append(":" + dep.name)
        else:
            raise _UnsupportedLowering(
                f"{name}: supply --java-dep {name}=LABEL for this dependency"
            )
    return list(dict.fromkeys(labels))


def _pom_edges(dist, suite, mappings, pending):
    compile_labels = []
    runtime_labels = []

    def visit(group, runtime_only):
        for dep in group.deps:
            runtime = runtime_only or group.is_runtime_dependency(dep)
            if (
                dep.suite != suite
                and dep.qualifiedName() not in mappings
                and dep.isPOMDistribution()
                and dep.name not in dep.suite.suiteDict.get("distributions", {})
                and not getattr(dep, "maven", False)
            ):
                # Private groups registered by suite extensions depend on which
                # optional suites mx loaded. Use the resolved edges and their scopes.
                visit(dep, runtime)
            else:
                labels = runtime_labels if runtime else compile_labels
                labels.extend(_dependency_labels([dep], suite, mappings, pending, True))

    visit(dist, False)
    compiled = list(dict.fromkeys(compile_labels))
    runtime = list(dict.fromkeys(label for label in runtime_labels if label not in compiled))
    return {"deps": compiled, **({"runtime_deps": runtime} if runtime else {})}


@cache
def _source_packages(project):
    """Index checked-in Java packages without resolving binary library contents."""
    packages = set()
    for directory in map(Path, project.source_dirs()):
        for source in directory.rglob("*.java"):
            if source.name != "module-info.java" and source.parent != directory:
                packages.add(".".join(source.relative_to(directory).parts[:-1]))
    return frozenset(packages)


def _source_dependencies(graph, suite, mappings, distributions, imported):
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
            for project in members:
                if type(project).__name__ == "EspressoSVMShared":
                    packages[dep].update(
                        project.substitute_package_name(pkg)
                        for original in project.shaded_deps()
                        for pkg in _source_packages(original)
                    )
        return packages[dep]

    for name, record in graph.items():
        if not record.get("sources") and not imported:
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
                for pkg in (
                    imported[dep.qualifiedName()]
                    if dep.qualifiedName() in imported
                    else provided(dep)
                ):
                    owners.setdefault(pkg, set()).update(labels)
        direct = set(record["deps"])
        source_files = record.get("sources")
        if source_files is None:
            source_files = [
                p.relative_to(suite.dir)
                for d in record.get("dirs", [])
                for p in (Path(suite.dir) / d).glob("*.java")
            ]
        resolve = {}
        for src in source_files:
            for qualified in imports.findall(
                (Path(suite.dir) / src).read_text(encoding="utf-8")
            ):
                parts = qualified.split(".")[:-1]
                while parts:
                    pkg = ".".join(parts)
                    if pkg in owners:
                        direct.update(owners[pkg])
                        if imported and len(owners[pkg]) == 1:
                            target = next(iter(owners[pkg]))
                            if target.startswith(("//", "@")):
                                resolve[pkg] = target
                        break
                    parts.pop()
        record["deps"] += sorted(direct.difference(record["deps"]))
        if resolve and not record.get("sources"):
            record["resolve"] = resolve


def _module_info(dist):
    info = getattr(dist, "moduleInfo", None)
    if info:
        return info
    name = getattr(dist, "moduleName", None)
    return {"name": name} if name else {}


def _module_reads_from_projects(graph, suite, mappings, distributions):
    """Promote direct module reads from the compiled project dependencies."""
    module_by_label = {}
    for qualified, labels in mappings.items():
        dep = mx.dependency(qualified)
        if dep.isJARDistribution() and _module_info(dep):
            for label in labels:
                module_by_label[label] = _module_info(dep)["name"]
    for name, record in distributions.items():
        if "module" in record:
            module_by_label[":" + name] = record["module"]

    for name, record in distributions.items():
        if "module" not in record:
            continue
        dist = mx.distribution(f"{suite.name}:{name}")
        available = set()
        pending = list(dist.deps)
        while pending:
            dep = pending.pop()
            if not dep.isJARDistribution() or not _module_info(dep):
                continue
            module = _module_info(dep)["name"]
            if module not in available:
                available.add(module)
                pending.extend(dep.deps)
        reads = {
            module_by_label[label]
            for member in dist.archived_deps()
            for label in graph.get(member.name, {}).get("deps", [])
            if label in module_by_label
        }
        declared = {
            directive.split()[-1]
            for directive in record["directives"]
            if directive.startswith("requires ")
        }
        extra = (available & reads) - declared - {record["module"]}
        record["directives"] = sorted(
            record["directives"] + ["requires " + module for module in extra]
        )


def _classpath_distribution(dist, suite, mappings, pending):
    if (
        type(dist).__name__ != "JARDistribution"
        or not dist.deps
        or dist.overlaps
        or dist.archiveparticipants
        or dist.is_stripped()
        or dist.use_module_path()
        or dist.excludedLibs
        or dist.optionalDependencies
        or dist.buildDependencies
        or dist.manifestEntries
        or getattr(dist, "javaProperties", None)
        or getattr(dist, "requiresConcealed", None)
    ):
        raise _UnsupportedLowering(
            f"{dist.name}: distribution requires an explicit --java-dep mapping"
        )
    return {
        "name": dist.name,
        "deps": _dependency_labels(dist.deps, suite, mappings, pending, True),
        "testonly": dist.is_test_distribution(),
    }


def _module_directives(dist, members, external, archives, mappings, library_modules):
    info = _module_info(dist)
    if set(info) - {
        "name",
        "requires",
        "exports",
        "opens",
        "uses",
        "requiresConcealed",
    }:
        raise _UnsupportedLowering(f"{dist.name}: unsupported moduleInfo fields")
    # mx adds observable distribution modules and preserves transitive reads
    # from their declared module descriptors. Explicit requires take precedence.
    requires = {}
    seen = set()
    visited = set()

    def visit(dep):
        if dep in visited:
            return
        visited.add(dep)
        if _module_info(dep):
            seen.add(dep)
        if dep.suite == dist.suite and dep.name not in archives:
            raise _UnsupportedLowering(
                f"{dist.name}: module path needs an explicit --archive-dist {dep.name}"
            )
        for child in dep.deps:
            if child.isJARDistribution():
                visit(child)

    for dep in external:
        if dep.isLibrary() and library_modules.get(dep.qualifiedName(), "") is None:
            if dep.qualifiedName() not in mappings:
                mx.abort(
                    f"{dep.qualifiedName()}: --platform-library needs a --java-dep mapping"
                )
            continue
        module = getattr(dep, "moduleName", None) or library_modules.get(
            dep.qualifiedName()
        )
        if dep.isLibrary() and module and dep.qualifiedName() in mappings:
            requires[module] = module
        else:
            visit(dep)
            # mx traverses unnamed distributions for their dependencies but
            # only emits a requires edge for distributions declaring a module.
            if info_dep := _module_info(dep):
                requires[info_dep["name"]] = info_dep["name"]
    observable = {_module_info(d)["name"] for d in seen}
    for dep in seen:
        for req in _module_info(dep).get("requires", []):
            if req.split()[-1] in observable and "transitive" in req.split()[:-1]:
                requires[req.split()[-1]] = req
    for project in members:
        for req in getattr(project, "requires", []):
            requires.setdefault(req, req)
        if project.isJavaProject():
            for module in project._orig_attrs.get("requiresConcealed", {}):
                if module != "java.base" and module.startswith(("java.", "jdk.")):
                    requires.setdefault(module, module)
    for req in info.get("requires", []):
        requires[req.split()[-1]] = req

    directives = {"requires " + req for req in requires.values()}
    directives.update("uses " + entry for entry in info.get("uses", []))
    directives.update(
        "uses " + entry for p in members for entry in getattr(p, "uses", [])
    )
    exports = info.get("exports", [])
    if not getattr(dist, "moduleInfo", None):
        exports = [
            entry
            for project in members
            if project.isJavaProject()
            for entry in getattr(project, "exports", sorted(_source_packages(project)))
        ]
    for kind, entries in (("exports", exports), ("opens", info.get("opens", []))):
        packages = {}
        for entry in entries:
            subject, to, names = " ".join(entry.split()).partition(" to ")
            matches = []
            for pattern in (name.strip() for name in subject.split(",")):
                # Query source packages directly when possible. Binary libraries and
                # shaded sources must expand wildcards against the final JAR entries.
                if pattern.endswith("*") and not any(
                    p.isLibrary() or getattr(p, "shadedDependencies", None)
                    for p in members
                ):
                    found = sorted(
                        {
                            pkg
                            for p in members
                            if p.isJavaProject()
                            for pkg in _source_packages(p)
                            if pkg.startswith(pattern[:-1])
                        }
                    )
                    if not found:
                        raise _UnsupportedLowering(
                            f"{dist.name}: no packages match {pattern}"
                        )
                    matches.extend(found)
                else:
                    matches.append(pattern)
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
        or (
            type(dist).__name__ != "JARDistribution"
            and (type(dist).__module__, type(dist).__name__)
            != ("mx_espresso", "EspressoIODistribution")
        )
        or dist.is_stripped()
        or dist.buildDependencies
        or any(
            d not in dist.deps
            or ("static " + getattr(d, "moduleInfo", {}).get("name", ""))
            not in getattr(dist, "moduleInfo", {}).get("requires", [])
            for d in dist.optionalDependencies
        )
        or dist.manifestEntries
        # mx passes javaProperties on the command line of a JVM that uses the
        # distribution; they do not change the JAR, so they do not block assembly.
        or dist.module_info_compilation_participants
    ):
        raise _UnsupportedLowering(
            f"{dist.name}: unsupported JAR archive configuration: {sorted(participants)}"
        )
    members = dist.archived_deps()
    if any(
        not (
            d.isJavaProject()
            or d.isLibrary()
            or type(d).__name__ == "LayoutDirDistribution"
            or d.qualifiedName() in mappings
        )
        for d in members
    ):
        raise _UnsupportedLowering(f"{dist.name}: unsupported archive member")
    external = [d for d in dist.deps if d not in members] + dist.excludedLibs
    packaged = {
        member
        for dep in external
        if dep.isJARDistribution()
        for member in dep.archived_deps()
    }
    # An unmapped native distribution of another suite, such as an LLVM toolchain that a
    # Java property locates, is neither a classpath entry nor a module.
    external = [
        d
        for d in external
        if not (d.isJavaProject() and d in packaged)
        and not (
            d.isDistribution()
            and not d.isJARDistribution()
            and d.suite != dist.suite
            and d.qualifiedName() not in mappings
        )
    ]
    base = [d for d in members if not getattr(d, "multiReleaseJarVersion", None)]
    overlays = {
        ":" + d.name: str(d.multiReleaseJarVersion) for d in members if d not in base
    }
    pending.extend(d for d in members if d not in base)
    if release is None:
        compliance = dist.maxJavaCompliance()
        release = max(9, compliance.value) if compliance else 9
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
    libraries = _dependency_labels(
        [d for d in base if d.isLibrary() or d.isJARDistribution()],
        suite,
        mappings,
        pending,
        True,
    )
    if libraries:
        result["archived_libraries"] = libraries
    if pattern := getattr(suite, "snippetsPattern", None):
        result["snippets_pattern"] = pattern
    concealed = getattr(dist, "requiresConcealed", {})
    concealed = {
        **concealed,
        **getattr(dist, "moduleInfo", {}).get("requiresConcealed", {}),
    }
    if concealed:
        result["add_exports"] = sorted(
            f"{mod}/{pkg}" for mod, packages in concealed.items() for pkg in packages
        )
    if _module_info(dist):
        project_exports = {
            f"{module}/{package}"
            for project in members
            if project.isJavaProject()
            for module, packages in project._orig_attrs.get(
                "requiresConcealed", {}
            ).items()
            for package in packages
        }
        if project_exports:
            result["module_exports"] = sorted(project_exports)
        result["module"] = _module_info(dist)["name"]
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


def _is_shaded_project(dep):
    return (type(dep).__module__, type(dep).__name__) == (
        "mx_sdk_shaded",
        "ShadedLibraryProject",
    )


def _default_native(project):
    return (type(project).__module__, type(project).__name__) in {
        ("mx._impl.mx_native", "DefaultNativeProject"),
        ("mx_espresso", "CustomLibJVMLinking"),
    } and project.kind == "shared_lib"


def _native_shared_recipe(project, pending):
    from mx._impl.mx_native import TargetSpec, MultitargetSpec

    attrs = project._orig_attrs
    supported = {
        "subDir",
        "native",
        "deliverable",
        "platformDependent",
        "os_arch",
        "os",
        "use_jdk_headers",
        "buildDependencies",
        "license",
        "testProject",
    }
    static_names = []
    if type(project).__name__ == "CustomLibJVMLinking":
        if not hasattr(project, "jdk_static_library_names"):
            raise _UnsupportedLowering(
                f"{project.name}: mx must export the force-loaded JDK libraries"
            )
        static_names = sorted(project.jdk_static_library_names())
        supported |= {"class", "dir"}
    if (
        attrs.keys() - supported
        or not _resource_path(project.deliverable)
        or "/" in project.deliverable
    ):
        raise _UnsupportedLowering(
            f"{project.name}: native project needs additional settings"
        )
    sources = project._source["files"]
    if sources.keys() - {".h", ".c", ".cc", ".cpp", ".S"}:
        raise _UnsupportedLowering(
            f"{project.name}: unsupported default native sources"
        )
    profiles = {}
    root = Path(project.dir).relative_to(project.suite.dir).as_posix()
    for system, arch, libc in (
        ("darwin", "aarch64", "default"),
        ("linux", "aarch64", "glibc"),
        ("linux", "amd64", "glibc"),
    ):
        platform = attrs.get("os_arch", {})
        archs = platform.get(system, platform.get("<others>", {}))
        branch = archs.get(arch, archs.get("<others>", {}))
        systems = attrs.get("os", {})
        system_branch = systems.get(system, systems.get("<others>", {}))
        if set(branch) & set(system_branch):
            raise _UnsupportedLowering(f"{project.name}: overlapping os and os_arch compiler options")
        branch = system_branch | branch
        if branch.get("ignore"):
            continue
        if branch.keys() - {"cflags", "ldflags", "ldlibs", "multitarget"}:
            raise _UnsupportedLowering(
                f"{project.name}: unsupported native settings for {system}"
            )
        target = TargetSpec(dict(os=system, arch=arch, libc=libc))
        mt = branch.get("multitarget", {})
        specs = mt if isinstance(mt, list) else [mt]
        if not any(
            MultitargetSpec(
                {"os": [system], "arch": [arch], "libc": [libc]} | spec
            ).matches(target)
            and {"host", "*"} & set(spec.get("compiler", ["host"]))
            for spec in specs
        ):
            continue
        filename = (
            "lib" + project.deliverable + (".dylib" if system == "darwin" else ".so")
        )
        link_inputs, linkopts = [], []
        for flag in branch.get("ldflags", []) + branch.get("ldlibs", []):
            own = "<path:" + project.qualifiedName() + ">/"
            if own in flag:
                prefix, rel = flag.split(own, 1)
                if not _resource_path(rel) or not (Path(project.dir) / rel).is_file():
                    raise _UnsupportedLowering(
                        f"{project.name}: missing linker input {rel}"
                    )
                link_inputs.append(root + "/" + rel)
                flag = prefix + "$(location " + link_inputs[-1] + ")"
            if "<" in flag:
                raise _UnsupportedLowering(
                    f"{project.name}: unresolved linker flag {flag}"
                )
            linkopts.append(flag)
        copts = branch.get("cflags", [])
        if any("<" in flag for flag in copts):
            raise _UnsupportedLowering(f"{project.name}: unresolved compiler flags")
        profile = (
            {"darwin": "macos", "linux": "linux"}[system]
            + "_"
            + {"aarch64": "arm64", "amd64": "amd64"}[arch]
        )
        profiles[profile] = {
            "filename": filename,
            "member": target.subdir + "/" + filename if mt else filename,
            "copts": copts,
            "linkopts": linkopts,
            "link_inputs": link_inputs,
        }
    if not profiles:
        raise _UnsupportedLowering(f"{project.name}: no compatible native toolchain")
    deps, jni = [], []
    for name in attrs.get("buildDependencies", []):
        dep = mx.dependency(name, context=project)
        if dep.suite == project.suite and dep.isJavaProject() and dep._orig_attrs.get("jniHeaders"):
            pending.append(dep)
            jni.append(":" + dep.name + ".jni_headers")
            continue
        if dep.suite != project.suite or not _default_native(dep):
            raise _UnsupportedLowering(
                f"{project.name}: needs native dependency {name}"
            )
        pending.append(dep)
        deps.append(":" + dep.name)
    return {
        "name": project.name,
        "root": root,
        "jni": jni,
        "use_jdk_headers": attrs.get("use_jdk_headers", False),
        "profiles": profiles,
        "deps": deps,
        "jdk_static_libraries": static_names,
        "srcs": sorted(
            root + "/" + f
            for ext, files in sources.items()
            if ext != ".h"
            for f in files
        ),
        "headers": sorted(root + "/" + f for f in sources.get(".h", [])),
        "testonly": project.is_test_project(),
    }


def _native_property_label(owner, expression):
    """Resolve an mx archive member to the shared library that produces it."""
    match = re.fullmatch(r"<path:([^>]+)>/<lib:([\w.-]+)>", expression)
    if not match:
        return None
    dist = mx.dependency(match[1], context=owner)
    if type(dist).__name__ != "LayoutTARDistribution" or dist.suite != owner.suite:
        return None
    candidates = []
    for path, source in mx.LayoutDistribution._walk_static_layout(
        dist._orig_attrs.get("layout", {}), dist.name, distribution_object=dist, context=dist
    ):
        if path != "./" or source["source_type"] != "dependency" or source.get("path"):
            continue
        dep = mx.dependency(source["dependency"], context=dist)
        if _default_native(dep) and dep.deliverable == match[2] and dep.suite == owner.suite:
            candidates.append(dep)
    return candidates[0] if len(candidates) == 1 else None


def _release_file(dep):
    return (type(dep).__module__, type(dep).__name__) == (
        "mx_espresso",
        "EspressoReleaseFileProject",
    )


def _release_file_recipe(dep, options):
    if not options.standalone_source_provenance or not options.standalone_jdk_release:
        raise _UnsupportedLowering(
            f"{dep.name}: bind the source provenance and target JDK release file"
        )
    suites = sorted(s.name for s in mx.suites())
    version = mx.suite("sdk").release_version_from_suite()
    if not version:
        raise _UnsupportedLowering(f"{dep.name}: no declared GraalVM version")
    return {
        "name": dep.name,
        "filename": dep.output_file_name(),
        "parent_release": options.standalone_jdk_release,
        "provenance": options.standalone_source_provenance,
        "suites": suites,
        "graalvm_version": version,
        "testonly": dep.is_test_project(),
    }


def _platform_file(dep):
    return callable(getattr(dep, "contents_for_platform", None)) and callable(
        getattr(dep, "output_file_name", None)
    )


def _platform_file_recipe(dep):
    filename = dep.output_file_name()
    if not _resource_path(filename) or "/" in filename or dep.deps or getattr(dep, "buildDependencies", []):
        raise _UnsupportedLowering(f"{dep.name}: unsupported generated file inputs")
    return {
        "name": dep.name,
        "filename": filename,
        "variants": {
            key: dep.contents_for_platform(os_name, libc)
            for key, os_name, libc in (
                ("linux", "linux", "glibc"), ("linux-musl", "linux", "musl"),
                ("macos", "darwin", None), ("windows", "windows", None),
            )
        },
        "testonly": dep.is_test_project(),
    }


def _source_project(dep):
    kind = (type(dep).__module__, type(dep).__name__)
    return kind in {
        ("mx_sdk_vm_ng", "StandaloneLicenses"),
        ("mx_sulong_suite_constituents", "HeaderProject"),
        ("mx_sulong_suite_constituents", "DocumentationProject"),
    }


def _wat_project(dep):
    return (type(dep).__module__, type(dep).__name__) == (
        "mx_wasm",
        "GraalVmWatProject",
    )


def _emscripten_project(dep):
    return (type(dep).__module__, type(dep).__name__) == (
        "mx_wasm",
        "EmscriptenProject",
    )


def _wasm_programs(project, options):
    if not options.wat2wasm or not options.wasm2wat:
        raise _UnsupportedLowering(f"{project.name}: bind --wat2wasm and --wasm2wat")
    source = Path(project.getSourceDir())
    programs = sorted(
        (Path(d) / file).relative_to(source).as_posix()
        for d, file in project.getProgramSources()
        if not file.startswith("_")
    )
    task = project.getBuildTask(mx.get_opts())
    benchmark = project.isBenchmarkProject()
    copts, exports, native = {}, {}, []
    for case in programs:
        if case.endswith(".c"):
            content = (source / case).read_text()
            flags = [
                flag
                for line in re.findall(r"//\s*CFLAGS\s*=\s*(.*)", content)
                for flag in line.split()
            ]
            if flags:
                copts[case] = flags
            exports[case] = (
                task.benchmark_methods()
                if benchmark
                else task.test_methods(str((source / case).with_suffix(".opts")))
            )
            choice = re.search(r"//\s*NATIVE_BENCH\s*=\s*(.*)", content)
            if benchmark and (not choice or choice[1].strip().lower() == "true"):
                native.append(case)
    return {
        "name": project.name,
        "root": source.relative_to(project.suite.dir).as_posix(),
        "cases": programs,
        "copts": copts,
        "exports": exports,
        "native": native,
        "includes": "includes/" + project.includeset
        if hasattr(project, "includeset")
        else "",
        "sidecars": sorted(
            (Path(d) / file).relative_to(source).as_posix()
            for d, file in project.getSources()
            if file.endswith((".opts", ".result"))
        ),
        "wat2wasm": options.wat2wasm,
        "wasm2wat": options.wasm2wat,
        "testonly": project.is_test_project(),
    }


def _project_files(project):
    members = {}
    if type(project).__name__ == "StandaloneLicenses" or project.results is not None:
        results = project.getArchivableResults()
    else:
        # These source-only projects expose their directory to build consumers.
        # mx declares no archive members when there is no results list.
        results = []
    root = Path(project.suite.dir)
    for source, member in results:
        file = Path(source)
        if (
            not file.is_relative_to(root)
            or not file.is_file()
            or not _resource_path(member)
        ):
            raise _UnsupportedLowering(
                f"{project.name}: missing or external project result {member}"
            )
        members[file.relative_to(root).as_posix()] = member
    files = []
    if not members and type(project).__name__ != "StandaloneLicenses":
        for directory in project.source_dirs():
            files.extend(
                p.relative_to(root).as_posix()
                for p in Path(directory).rglob("*")
                if p.is_file() and p.name not in {"BUILD", "BUILD.bazel"}
            )
    return {
        "name": project.name,
        "srcs": sorted(files),
        "members": members,
        "testonly": project.is_test_project(),
    }


def _thin_launcher(project):
    return (type(project).__module__, type(project).__name__) == (
        "mx_sdk_vm_ng",
        "ThinLauncherProject",
    )


def _thin_launcher_recipe(project):
    # mx computes the language home, extracted libraries, option arrays and main
    # module from the launcher and its resolved Java distributions.
    if project.setup_relative_resources:
        raise _UnsupportedLowering(
            f"{project.name}: thin launcher requires an extracted resource distribution"
        )
    if project._cflags or project._ldflags or project._ldlibs:
        raise _UnsupportedLowering(
            f"{project.name}: custom thin launcher flags require a target toolchain mapping"
        )
    flags = []
    for flag in project.cflags:
        if flag.startswith(("-DLAUNCHER_", "-DGRAALVM_VERSION=", "-DLIBLANG_RELPATH=")):
            (argument,) = shlex.split(flag)
            if "PATH" in argument.split("=", 1)[0]:
                argument = argument.replace(os.sep, "/")
            flags.append(argument)
    supported = {}
    for system, library_suffix in (("linux", ".so"), ("macos", ".dylib")):
        copts = [
            re.sub(r"<lib:([a-zA-Z0-9_.+-]+)>", r"lib\1" + library_suffix, flag)
            for flag in flags
        ]
        if any("<" in flag or ">" in flag for flag in copts):
            raise _UnsupportedLowering(
                f"{project.name}: unbound thin launcher substitution"
            )
        jre = project.relative_jre_path.replace(os.sep, "/")
        copts.append(f"-DLIBJVM_RELPATH={jre}/lib/server/libjvm{library_suffix}")
        if system == "macos":
            copts.append(f"-DLIBJLI_RELPATH={jre}/lib/libjli.dylib")
        supported[system] = copts
    sdk = mx.suite("sdk")
    src_dir = Path(project.dir) / "src"
    sources = sorted(src_dir.glob("*.cc"))
    if not sources:
        raise _UnsupportedLowering(
            f"{project.name}: thin launcher sources are missing from {src_dir}"
        )
    return {
        "name": project.name,
        "srcs": [
            "//" + sdk.name + ":" + src.relative_to(sdk.dir).as_posix()
            for src in sources
        ],
        "platform_copts": supported,
    }


def _svm_macro_components(names):
    import mx_sdk_vm

    components = {
        component.short_name: component for component in mx_sdk_vm.graalvm_components()
    }
    todo = list(names)
    seen = set()
    jars = {}
    layouts = {}
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        component = components.get(name)
        if isinstance(component, mx_sdk_vm.GraalVMSvmMacro):
            directory = "lib/svm/macros/" + component.dir_name
        elif isinstance(component, mx_sdk_vm.GraalVmTruffleLibrary):
            directory = "lib/" + component.dir_name
        else:
            raise _UnsupportedLowering(
                f"Unsupported Native Image macro component {name}"
            )
        for dep in component.builder_jar_distributions:
            dist = mx.distribution(dep)
            jars[dist.qualifiedName()] = (
                directory + "/builder/" + dist.default_filename()
            )
        for dep in component.support_distributions:
            layouts[mx.distribution(dep).qualifiedName()] = directory
        todo.extend(component.dependency_names)
    return {"builder_jars": jars, "support_layouts": layouts}


def _native_image_executable(dep):
    return (type(dep).__module__, type(dep).__name__) == (
        "mx_sdk_vm_ng",
        "NativeImageExecutableProject",
    )


def _language_library(dep):
    return (type(dep).__module__, type(dep).__name__) == (
        "mx_sdk_vm_ng",
        "LanguageLibraryProject",
    )


def _native_tool_layout(dist):
    return (type(dist).__module__, type(dist).__name__) == (
        "mx_sdk_vm_ng",
        "ToolchainToolDistribution",
    )


def _bash_launcher(project):
    return (type(project).__module__, type(project).__name__) == (
        "mx_sandbox_toolchains",
        "ToolchainBashLauncherProject",
    )


def _bash_launcher_recipe(project, mappings, pending, jdk_archive):
    if not jdk_archive:
        raise _UnsupportedLowering(f"{project.name}: bind the target JDK archive")
    if getattr(project, "buildDependencies", ()) or not all(
        isinstance(dep, mx.ClasspathDependency) for dep in project.deps
    ):
        raise _UnsupportedLowering(
            f"{project.name}: launcher needs additional build inputs"
        )
    paths = {}

    def render(entries, cp_prefix=None, cp_suffix=None, jdk=None):
        if (
            cp_prefix
            or cp_suffix
            or not all(dep.isJARDistribution() or dep.isLibrary() for dep in entries)
        ):
            raise _UnsupportedLowering(
                f"{project.name}: undeclared launcher classpath input"
            )
        marker = f"MX_BAZEL_PATH_{len(paths)}"
        paths[marker] = _dependency_labels(
            entries, project.suite, mappings, pending, True
        )
        return marker

    with mx.runtime_classpath_formatter(render):
        args = mx.get_runtime_jvm_args(project.deps, include_system_properties=False)
    result = {
        "name": project.name,
        "output": project.exeName,
        "jdk_archive": jdk_archive,
    }
    flags = []
    tokens = iter(args)
    for flag in tokens:
        if flag in ("-p", "-cp"):
            result["classpath" if flag == "-cp" else "modulepath"] = paths[next(tokens)]
        else:
            flags.append(flag)
    # The upstream launcher passes the invoked alias as its executablePath.
    result["java_args"] = (
        flags + ["-Dorg.graalvm.launcher.executablePath={argv0}"] + project.jvmArgs
    )
    return result


def _tool_links(dist, pending):
    tool = dist.tool_project
    if (
        not (_native_image_executable(tool) or _bash_launcher(tool))
        or tool.suite != dist.suite
    ):
        raise _UnsupportedLowering(
            f"{dist.name}: needs the packaged launcher {tool.qualifiedName()}"
        )
    aliases = {}
    main = tool.exeName if _bash_launcher(tool) else tool.base_file_name()
    if not _resource_path(main) or "/" in main:
        raise _UnsupportedLowering(f"{dist.name}: invalid executable name {main}")
    for name, alternates in dist.tool_links.items():
        for alias, target in [(name, main), *((alt, name) for alt in alternates)]:
            if (
                not _resource_path(alias)
                or "/" in alias
                or alias in aliases
                or alias == main
            ):
                raise _UnsupportedLowering(
                    f"{dist.name}: invalid or duplicate tool alias {alias}"
                )
            aliases[alias] = target
    pending.append(tool)
    # mx's Windows wrappers forward argv[0] directly to the executable.
    import mx_sdk_vm_ng

    return {
        "name": dist.name,
        "tool": ":" + tool.name,
        **({"launcher": main} if _bash_launcher(tool) else {}),
        "links": aliases,
        "windows_command": mx_sdk_vm_ng._make_windows_link(main + ".exe"),
    }


def _image_recipe(project, suite, mappings, pending):
    unsupported = project._orig_attrs.keys() - {
        "class",
        "dependencies",
        "defaultBuild",
        "build_args",
        "deliverable",
    }
    shared = _language_library(project)
    if shared:
        unsupported -= {"dynamicBuildArgs", "dynamicDependencies"}
    if unsupported:
        raise _UnsupportedLowering(
            f"{project.name}: unsupported native image settings {sorted(unsupported)}"
        )
    groups = {}

    def render(entries, cp_prefix=None, cp_suffix=None, jdk=None):
        if (
            cp_prefix
            or cp_suffix
            or not all(dep.isJARDistribution() or dep.isLibrary() for dep in entries)
        ):
            raise _UnsupportedLowering(
                f"{project.name}: undeclared native image classpath input"
            )
        marker = f"MX_BAZEL_CLASSPATH_{len(groups)}"
        groups[marker] = _dependency_labels(entries, suite, mappings, pending, True)
        return marker

    # Ask the same mx task that builds the image for its effective flags. Only
    # artifact locations and executor parallelism belong to Bazel.
    args = SimpleNamespace(
        alt_cc=None,
        alt_cl=None,
        alt_cflags=None,
        alt_ldflags=None,
        disable_debuginfo_stripping=False,
    )
    with mx.runtime_classpath_formatter(render):
        command = project.getBuildTask(args).get_build_args()
    recipe = {"name": project.name, "output": project.base_file_name(), "flags": []}
    if shared:
        if "--shared" not in command:
            raise _UnsupportedLowering(f"{project.name}: missing shared library image option")
        recipe["shared"] = True
        command = [arg for arg in command if arg != "--shared"]
    elif command and re.fullmatch(r"(?:[A-Za-z_$][\w$]*\.)+[A-Za-z_$][\w$]*", command[-1]):
        recipe["main"] = command.pop()
    else:
        raise _UnsupportedLowering(f"{project.name}: expected a Java main class for the native image")
    tokens = iter(command)
    for arg in tokens:
        if arg in ("-cp", "-p"):
            marker = next(tokens, None)
            if marker not in groups:
                raise _UnsupportedLowering(
                    f"{project.name}: unbound native image search path"
                )
            recipe["classpath" if arg == "-cp" else "modulepath"] = groups[marker]
        elif arg.startswith("--parallelism="):
            continue
        elif arg.startswith("@") or re.search(r"(?:^|=)(?:/|[A-Za-z]:[\\/])", arg):
            raise _UnsupportedLowering(
                f"{project.name}: native image option needs a declared input: {arg}"
            )
        else:
            recipe["flags"].append(arg)
    return recipe


def _standalone_archive(dep):
    return (type(dep).__module__, type(dep).__name__) == (
        "mx_sdk_vm_ng",
        "DeliverableStandaloneArchive",
    )


def _release_archive(dist, pending):
    source = dist.standalone_dir_dist
    if (
        len(dist.layout) != 1
        or source.suite != dist.suite
        or set(dist.buildDependencies) != {source}
    ):
        raise _UnsupportedLowering(
            f"{dist.name}: expected a single standalone installation"
        )
    ((root, layout),) = dist.layout.items()
    layout = dict(layout)
    layout.pop("_str_", None)
    if (
        layout.pop("optional", False)
        or layout
        != {
            "source_type": "dependency",
            "dependency": source.name,
            "path": "*",
            "dereference": "never",
        }
        or not root.endswith("/")
        or dist.remoteExtension() != "tar.gz"
    ):
        raise _UnsupportedLowering(
            f"{dist.name}: unsupported standalone archive recipe"
        )
    prefix = root[:-1].replace(
        "<version>", dist.path_substitutions.substitute("<version>")
    )
    if not _resource_path(
        prefix.replace("<graalvm_os>", "os").replace("<arch>", "arch")
    ):
        raise _UnsupportedLowering(f"{dist.name}: unsupported archive path {prefix}")
    pending.append(source)
    return {"name": dist.name, "install": ":" + source.name, "prefix": prefix}


def _can_lower_root(dep):
    return (
        type(dep).__name__
        in (
            "JavaProject",
            "LayoutDirDistribution",
            "LayoutTARDistribution",
            "LayoutJARDistribution",
        )
        or _standalone_archive(dep)
        or _native_tool_layout(dep)
        or _thin_launcher(dep)
        or _bash_launcher(dep)
        or _native_image_executable(dep)
        or _language_library(dep)
        or _default_native(dep)
        or _source_project(dep)
        or _platform_file(dep)
        or _release_file(dep)
        or _wat_project(dep)
        or _emscripten_project(dep)
        or _in_tree_sulong_tests(dep)
        or _is_shaded_project(dep)
        or dep.isJARDistribution()
        or dep.isPOMDistribution()
    )


def _source_archive_groups(dist):
    """mx stages .java files relative to each project's source root."""
    groups = {}
    for project in dist.archived_deps():
        if (
            type(project).__name__ != "JavaProject"
            or project.annotation_processors()
            or project.suite != dist.suite
        ):
            raise _UnsupportedLowering(
                f"{dist.name}: source archive requires generated or external sources"
            )
        version = getattr(project, "multiReleaseJarVersion", None)
        prefix = f"META-INF/versions/{version}/" if version else ""
        for directory in map(Path, project.source_dirs()):
            for file in sorted(directory.rglob("*.java")):
                rel = file.relative_to(dist.suite.dir).as_posix()
                groups[rel] = prefix + file.relative_to(directory).as_posix()
    if not groups:
        raise _UnsupportedLowering(f"{dist.name}: empty source archive")
    return groups


def _web_image_macro_layout(dist, pending, mappings):
    import mx_sdk_vm_impl

    (project,) = dist.deps
    filename = "native-image.properties"
    if dist.layout != {filename: "dependency:" + project.name + "/" + filename}:
        raise _UnsupportedLowering(f"{dist.name}: unsupported macro layout")
    task = project.getBuildTask(mx.get_opts())
    lines = [
        "# This file is auto-generated",
        "ExcludeFromAll = true",
        "ProvidedHostedOptions = " + " ".join(project.provided_hosted_options),
    ]
    for key, dists in (
        ("ImageBuilderModulePath", project.builder_dists),
        ("ImageProvidedJars", project.image_provided_jars),
    ):
        if dists:
            lines.append(
                key
                + " = "
                + task._escaped_macro_paths(task._macro_relative_paths(dists))
            )
    builders = _dependency_labels(
        [mx.dependency(n) for n in project.builder_dists],
        dist.suite,
        mappings,
        pending,
        True,
    )
    name = project.name
    return {
        "name": dist.name,
        "members": {":" + name: filename},
        "archive_imports": {},
        "testonly": dist.is_test_distribution(),
        "macro": {
            "name": name,
            "lines": lines,
            "java_args": [
                mx_sdk_vm_impl.java_properties_escape(arg) for arg in project.java_args
            ],
            "builders": builders,
        },
    }


def _source_layout_branch(dist, layout, os_name):
    members, imports, paths = {}, {}, {}
    projects = []
    for destination, source in mx.LayoutDistribution._walk_static_layout(
        layout, dist.name, distribution_object=dist, context=dist
    ):
        directory = destination.endswith("/")
        dest = destination.removeprefix("./")
        path = source.get("path")
        kind = source["source_type"]
        keys = source.keys() - {"source_type", "_str_"}
        if (
            kind == "file"
            and keys == {"path"}
            and _resource_path(path)
            and (Path(dist.suite.dir) / path).is_file()
        ):
            entries = {path: dest + Path(path).name if directory else dest}
            dep = None
        elif (
            kind == "dependency"
            and directory
            and path
            and keys == {"dependency", "path", "optional"}
            and not source["optional"]
        ):
            dep = mx.dependency(
                source["dependency"], fatalIfMissing=False, context=dist
            )
            if dep is None:
                # mx can omit a project from its global registry on the export host.
                dep = next((p for p in dist.suite.projects if source["dependency"] in (p.qualifiedName(), p.name)), None)
            if not dep or dep.suite != dist.suite:
                return None, []
            entries = {}
            if _default_native(dep):
                pattern = "<lib:" + dep.deliverable + ">"
                if path not in (pattern, "*/<multitarget_libc_selection>/" + pattern) or os_name not in ("linux", "darwin"):
                    return None, []
                filename = "lib" + dep.deliverable + (".so" if os_name == "linux" else ".dylib")
                # mx installs the one target-selected shared library by basename.
                if path == pattern:
                    entries[filename] = dest + filename
                else:
                    entries["*/" + ("glibc" if os_name == "linux" else "default") + "/" + filename] = dest + filename
            elif _source_project(dep):
                for member in _project_files(dep)["members"].values():
                    matched = glob_match(path, member)
                    if matched is not None:
                        prefix = matched.rpartition("/")[0]
                        entries[member] = dest + (
                            member.removeprefix(prefix + "/") if prefix else member
                        )
            else:
                return None, []
            projects.append(dep)
            if not entries:
                raise _UnsupportedLowering(
                    f"{dist.name}: no results for {source['_str_']}"
                )
        else:
            raise _UnsupportedLowering(
                f"{dist.name}: unsupported {os_name} source {source['_str_']}"
            )
        for src, target in entries.items():
            if target in paths or not _resource_path(target):
                raise _UnsupportedLowering(
                    f"{dist.name}: duplicate or invalid member {target}"
                )
            paths[target] = None
            if dep:
                imports.setdefault(":" + dep.name, []).append(src + "=" + target)
            else:
                members.setdefault(src, []).append(target)
    return {
        "members": {k: "\n".join(v) for k, v in members.items()},
        "archive_imports": {k: "\n".join(v) for k, v in imports.items()},
        "file_list": list(paths) if dist.fileListPurpose else [],
    }, projects


def _os_layout(dist, pending):
    """Lower OS-specific source layouts without expanding them for the Gazelle host."""
    attrs = dist._orig_attrs
    if (
        attrs.keys()
        & {"arch", "layout", "buildDependencies", "dependencies"}
        or dist.excludedLibs
        or dist.hashEntry
        or dist.fileListEntry
        or dist.compress
    ):
        raise _UnsupportedLowering(
            f"{dist.name}: needs additional platform layout inputs"
        )
    systems = attrs.get("os")
    if systems is None:
        systems = {}
        for system, architectures in attrs["os_arch"].items():
            if architectures.keys() != {"<others>"}:
                raise _UnsupportedLowering(f"{dist.name}: layout varies by target architecture")
            systems[system] = architectures["<others>"]
    recipes = {}
    for os_name, bazel_os in (
        ("linux", "linux"),
        ("darwin", "macos"),
        ("windows", "windows"),
    ):
        branch = systems.get(os_name, systems.get("<others>", {}))
        if branch.keys() != {"layout"}:
            raise _UnsupportedLowering(f"{dist.name}: expected a layout for {os_name}")
        recipe, projects = _source_layout_branch(dist, branch["layout"], os_name)
        if recipe is None:
            if os_name == "windows":
                continue
            raise _UnsupportedLowering(
                f"{dist.name}: needs native project results for {os_name}"
            )
        pending.extend(projects)
        recipes[bazel_os] = recipe
    if "linux-musl" in systems:
        branch = systems["linux-musl"]
        if branch.keys() != {"layout"}:
            raise _UnsupportedLowering(f"{dist.name}: unsupported musl layout")
        recipe, projects = _source_layout_branch(dist, branch["layout"], "linux-musl")
        if recipe is None:
            raise _UnsupportedLowering(f"{dist.name}: needs native project results for musl")
        pending.extend(projects)
        recipes["linux-musl"] = recipe
    return {
        "name": dist.name,
        "format": "dir" if type(dist).__name__ == "LayoutDirDistribution" else "tar",
        "os_layouts": recipes,
        "testonly": dist.is_test_distribution(),
    }


def _tar_layout(dist, pending, mappings, archives):
    if len(dist.deps) == 1 and (
        type(dist.deps[0]).__module__,
        type(dist.deps[0]).__name__,
    ) == ("mx_web_image", "WebImageMacroBuilder"):
        return _web_image_macro_layout(dist, pending, mappings)
    declaration = dist._orig_attrs
    if dist.isPlatformDependent() and "<multitarget_libc_selection>" in str(declaration.get("layout", {})):
        return _platform_native_layout(dist, pending)
    # mx also uses platformDependent to choose the staging directory. A fixed
    # layout still produces the same member paths regardless of the Gazelle host.
    platform_layout = declaration.keys() & {"os", "arch", "os_arch", "platforms"}
    if (
        (
            dist.isPlatformDependent()
            and (platform_layout or "<" in str(declaration.get("layout", {})))
        )
        or dist.compress
        or dist.deps
        or dist.excludedLibs
        or dist.fileListEntry
        or dist.hashEntry
        or dist._removed_deps
    ):
        raise _UnsupportedLowering(
            f"{dist.name}: TAR layout needs a Bazel implementation bound with --provided-target"
        )
    members = {}
    source_archives = {}
    imports = {}
    destinations = {}
    inputs = set()
    for destination, source in dist._walk_layout():
        path = source.get("path")
        kind = source["source_type"]
        keys = set(source) - {"_str_", "source_type"}
        directory = destination.endswith("/")
        destination = destination.removeprefix("./")
        if kind == "file" and keys == {"path"} and _resource_path(path):
            if not (Path(dist.suite.dir) / path).is_file():
                raise _UnsupportedLowering(f"{dist.name}: missing TAR input {path}")
            target = path
            if directory:
                destination += Path(path).name
        elif (
            kind in ("dependency", "extracted-dependency")
            and keys == {"dependency", "path", "optional"}
            and not source["optional"]
        ):
            dep = mx.dependency(source["dependency"], context=dist)
            inputs.add(dep)
            if (
                kind == "dependency"
                and path == "*"
                and directory
                and dep.suite == dist.suite
                and (_wat_project(dep) or _source_project(dep))
            ):
                if destination and not _resource_path(destination.rstrip("/")):
                    raise _UnsupportedLowering(
                        f"{dist.name}: invalid project result destination {destination!r}"
                    )
                pending.append(dep)
                imports.setdefault(":" + dep.name, []).append("=" + destination)
                continue
            if (
                kind == "dependency"
                and path in ("*", "*.jar")
                and dep.suite == dist.suite
                and type(dep).__name__ == "JARDistribution"
            ):
                pending.append(dep)
                for _, filename in dep.getArchivableResults():
                    if not fnmatch.fnmatchcase(filename, path):
                        continue
                    member = destination + filename if directory else destination
                    if not _resource_path(member) or member in destinations:
                        raise _UnsupportedLowering(
                            f"{dist.name}: duplicate or invalid archive path {member}"
                        )
                    destinations[member] = None
                    if filename == dep.default_filename():
                        archives.add(dep.name)
                        label = ":" + dep.name
                    elif filename == dep.default_source_filename():
                        name = dist.name + ".sources." + dep.name
                        source_archives[name] = _source_archive_groups(dep)
                        label = ":" + name
                    else:
                        raise _UnsupportedLowering(
                            f"{dist.name}: unsupported JAR result {filename}"
                        )
                    members.setdefault(label, []).append(member)
                continue
            labels = mappings.get(dep.qualifiedName())
            if labels is None and dep.suite == dist.suite and dep.isDistribution():
                pending.append(dep)
                labels = [":" + dep.name]
            if not labels or len(labels) != 1:
                raise _UnsupportedLowering(
                    f"{dist.name}: bind {dep.qualifiedName()} to one Bazel artifact"
                )
            target = labels[0]
            if kind == "extracted-dependency" and (not path or _resource_path(path)):
                if directory and path:
                    destination += Path(path).name
                elif directory and not path:
                    if destination and not _resource_path(destination.rstrip("/")):
                        raise _UnsupportedLowering(
                            f"{dist.name}: invalid TAR import destination {destination!r}"
                        )
                    imports.setdefault(target, []).append("=" + destination)
                    continue
                elif not path:
                    raise _UnsupportedLowering(
                        f"{dist.name}: extracting a full archive needs a destination directory"
                    )
                if not _resource_path(destination) or destination in destinations:
                    raise _UnsupportedLowering(
                        f"{dist.name}: expected a unique TAR member path: {destination!r}"
                    )
                destinations[destination] = None
                imports.setdefault(target, []).append(path + "=" + destination)
                continue
            if kind != "dependency" or path or directory:
                raise _UnsupportedLowering(
                    f"{dist.name}: unsupported TAR input {source['_str_']}"
                )
        else:
            raise _UnsupportedLowering(
                f"{dist.name}: unsupported TAR input {source['_str_']}"
            )
        if not _resource_path(destination) or destination in destinations:
            raise _UnsupportedLowering(
                f"{dist.name}: expected a unique TAR member path: {destination!r}"
            )
        destinations[destination] = None
        members.setdefault(target, []).append(destination)
    if not (members or imports) or inputs != set(dist.buildDependencies):
        raise _UnsupportedLowering(
            f"{dist.name}: expected a nonempty TAR layout with explicit inputs"
        )
    if dist.fileListPurpose and imports:
        raise _UnsupportedLowering(
            f"{dist.name}: file list requires resolved imported member order"
        )
    return {
        "name": dist.name,
        **({"source_archives": source_archives} if source_archives else {}),
        **({"file_list": list(destinations)} if dist.fileListPurpose else {}),
        **({"format": "jar"} if type(dist).__name__ == "LayoutJARDistribution" else {}),
        "members": {target: "\n".join(entries) for target, entries in members.items()},
        "archive_imports": {
            target: "\n".join(entries) for target, entries in imports.items()
        },
        "testonly": dist.is_test_distribution() or any(
            dep.isDistribution() and dep.is_test_distribution() for dep in inputs
        ),
    }


def _platform_native_layout(dist, pending):
    attrs = dist._orig_attrs
    metadata = bool(dist.fileListEntry and dist.hashEntry)
    if (
        not attrs.get("layout")
        or bool(dist.fileListEntry) != bool(dist.hashEntry)
        or dist.fileListPurpose or dist.deps or dist.excludedLibs or dist.compress
        or attrs.keys() & {"os", "arch", "os_arch"}
    ):
        raise _UnsupportedLowering(f"{dist.name}: unsupported native layout")
    profiles = {}
    for os_name, arch, profile in (
        ("darwin", "aarch64", "macos_arm64"),
        ("linux", "amd64", "linux_amd64"),
        ("linux", "aarch64", "linux_arm64"),
    ):
        if "platforms" in attrs and f"{os_name}-{arch}" not in attrs["platforms"]:
            continue

        def resolve(path):
            if path == "./":
                return ""
            result = path.removeprefix("./").replace("<os>", os_name).replace("<arch>", arch)
            if not _resource_path(result.rstrip("/")):
                raise _UnsupportedLowering(f"{dist.name}: invalid resource path {path}")
            return result

        imports, members = {}, {}
        for destination, source in mx.LayoutDistribution._walk_static_layout(
            attrs["layout"], dist.name, distribution_object=dist, context=dist
        ):
            dest = resolve(destination)
            path = source.get("path")
            if source["source_type"] == "file" and source.keys() == {"_str_", "source_type", "path"}:
                if not _resource_path(path) or not (Path(dist.suite.dir) / path).is_file():
                    raise _UnsupportedLowering(f"{dist.name}: missing layout file {path}")
                members.setdefault(path, []).append(dest + Path(path).name if destination.endswith("/") else dest)
                continue
            if (
                source["source_type"] != "dependency"
                or source.keys()
                - {"_str_", "source_type", "path", "dependency", "optional"}
            ):
                raise _UnsupportedLowering(
                    f"{dist.name}: unsupported resource source {source['_str_']}"
                )
            target = next(
                (
                    d
                    for d in dist.suite.projects + dist.suite.dists
                    if source["dependency"] in (d.name, d.qualifiedName())
                ),
                None,
            )
            if not target:
                raise _UnsupportedLowering(
                    f"{dist.name}: missing resource producer {source['dependency']}"
                )
            if _platform_file(target) and not path and not destination.endswith("/"):
                members.setdefault(":" + target.name, []).append(dest)
                pending.append(target)
                continue
            if not destination.endswith("/"):
                raise _UnsupportedLowering(f"{dist.name}: expected native layout directory")
            if (
                _default_native(target)
                and path
                == "*/<multitarget_libc_selection>/<lib:" + target.deliverable + ">"
            ):
                enabled = _native_shared_recipe(target, [])["profiles"]
                if profile not in enabled:
                    if source["optional"]:
                        continue
                    raise _UnsupportedLowering(
                        f"{dist.name}: required native input missing for {profile}"
                    )
                spec = "{primary}=" + dest + "{basename}"
            elif type(target).__name__ == "LayoutDirDistribution" and path == "*":
                spec = "=" + dest
            else:
                raise _UnsupportedLowering(
                    f"{dist.name}: unsupported resource member {source['_str_']}"
                )
            imports.setdefault(":" + target.name, []).append(spec)
            pending.append(target)
        profiles[profile] = {
            **({"members": {label: "\n".join(paths) for label, paths in members.items()}} if members else {}),
            "archive_imports": {
                label: "\n".join(specs) for label, specs in imports.items()
            },
            **({"file_list_entry": resolve(dist.fileListEntry), "hash_entry": resolve(dist.hashEntry)} if metadata else {}),
        }
    if not profiles:
        raise _UnsupportedLowering(f"{dist.name}: no supported resource platforms")
    return {
        "name": dist.name,
        "format": "dir" if type(dist).__name__ == "LayoutDirDistribution" else "tar",
        "os_layouts": profiles,
        "testonly": dist.is_test_distribution(),
    }


def _install_layout(dist, pending, mappings, options, bindings):
    attrs = dist._orig_attrs
    if (
        not dist.isPlatformDependent()
        or not attrs.get("layout")
        or attrs.keys() & {"os", "arch", "os_arch"}
        or dist.deps
        or dist.excludedLibs
        or dist.fileListEntry
        or dist.hashEntry
        or dist.fileListPurpose
    ):
        raise _UnsupportedLowering(
            f"{dist.name}: needs a target-specific install recipe"
        )
    members = {}
    imports = {}
    jdks = {}
    source_archives = {}
    inputs = set()
    # Keep platform substitutions symbolic until we know the supplying project.
    # Asking mx for _walk_layout() here would expand them for the Gazelle host.
    for destination, source in mx.LayoutDistribution._walk_static_layout(
        dist.layout, dist.name, distribution_object=dist, context=dist
    ):
        dest = destination.removeprefix("./")
        directory = destination.endswith("/")
        kind = source["source_type"]
        path = source.get("path")
        if source.get("optional") and kind in ("dependency", "extracted-dependency"):
            producer = mx.dependency(
                source["dependency"], fatalIfMissing=False, context=dist
            )
            if (
                producer is None
                or producer.name in dist._removed_deps
                or getattr(producer, "ignore", False)
            ):
                if producer in dist.buildDependencies:
                    inputs.add(producer)
                continue
        optional_project_layout = (
            kind in ("dependency", "extracted-dependency") and path == "*" and directory
        )
        if source.get("optional") and not optional_project_layout:
            raise _UnsupportedLowering(
                f"{dist.name}: optional install member {source['_str_']}"
            )
        if (
            kind == "classpath-dependencies"
            and directory
            and _resource_path(dest.rstrip("/"))
        ):
            if source.keys() - {"source_type", "_str_", "dependencies", "optional"}:
                raise _UnsupportedLowering(
                    f"{dist.name}: unsupported classpath install attributes"
                )
            roots = [mx.dependency(n, context=dist) for n in source["dependencies"]]
            inputs.update(roots)
            for entry in mx.classpath_entries(roots):
                if not entry.isJARDistribution():
                    raise _UnsupportedLowering(
                        f"{dist.name}: classpath needs the artifact {entry.qualifiedName()}"
                    )
                (target,) = _dependency_labels(
                    [entry], dist.suite, mappings, pending, True
                )
                members.setdefault(target, []).append(dest + entry.default_filename())
            continue
        if kind not in ("dependency", "extracted-dependency"):
            raise _UnsupportedLowering(
                f"{dist.name}: unsupported install source {source['_str_']}"
            )
        dep = mx.dependency(source["dependency"], context=dist)
        inputs.add(dep)
        allowed = {"_str_", "source_type", "dependency", "path", "optional"}
        if (
            kind == "dependency"
            and path == "*"
            and directory
            and (
                dep.qualifiedName() == "sdk:STANDALONE_JAVA_HOME"
                or (type(dep).__module__, type(dep).__name__)
                == ("mx_sdk_vm_ng", "JavaHomeDependency")
            )
        ):
            if (
                not options.standalone_jdk_archive
                or source.keys() - allowed - {"exclude"}
                or (dest and not _resource_path(dest.rstrip("/")))
            ):
                raise _UnsupportedLowering(
                    f"{dist.name}: bind the standalone JDK archive and installation path"
                )
            homes = jdks.setdefault(options.standalone_jdk_archive, [])
            if any(home["directory"] == dest for home in homes):
                raise _UnsupportedLowering(f"{dist.name}: duplicate JDK home {dest}")
            homes.append({"directory": dest, "exclude": source.get("exclude", [])})
            continue
        if source.keys() - allowed:
            raise _UnsupportedLowering(
                f"{dist.name}: unsupported install attributes for {source['_str_']}"
            )
        if (
            kind == "dependency"
            and path == "release"
            and (
                dep.qualifiedName() == "sdk:STANDALONE_JAVA_HOME"
                or (type(dep).__module__, type(dep).__name__)
                == ("mx_sdk_vm_ng", "JavaHomeDependency")
            )
        ):
            if not options.standalone_jdk_release:
                raise _UnsupportedLowering(
                    f"{dist.name}: bind the release file of STANDALONE_JAVA_HOME"
                )
            target = options.standalone_jdk_release
        else:
            if dep.suite == dist.suite:
                target = ":" + dep.name
                pending.append(dep)
            else:
                labels = mappings.get(dep.qualifiedName(), [])
                if len(labels) != 1:
                    raise _UnsupportedLowering(
                        f"{dist.name}: bind install input {dep.qualifiedName()}"
                    )
                target = labels[0]
            if (
                kind == "dependency"
                and directory
                and path
                and path.endswith("/*")
                and _resource_path(path[:-2])
                and type(dep).__name__ == "LayoutDirDistribution"
            ):
                imports.setdefault(target, []).append(path + "=" + dest)
                continue
            if directory and dest and not _resource_path(dest.rstrip("/")):
                raise _UnsupportedLowering(
                    f"{dist.name}: invalid install destination {dest}"
                )
            if (
                kind == "dependency"
                and directory
                and path == "*"
                and dep.isJARDistribution()
            ):
                for _, filename in dep.getArchivableResults():
                    if filename == dep.default_filename():
                        label = target
                    elif filename == dep.default_source_filename():
                        if dep.suite == dist.suite:
                            name = dist.name + ".sources." + dep.name
                            source_archives[name] = _source_archive_groups(dep)
                            label = ":" + name
                        else:
                            label = bindings["source_archives"].get(dep.qualifiedName())
                            if not label:
                                raise _UnsupportedLowering(
                                    f"{dist.name}: export source archive {dep.qualifiedName()}"
                                )
                    else:
                        raise _UnsupportedLowering(
                            f"{dist.name}: unknown JAR result {filename}"
                        )
                    members.setdefault(label, []).append(dest + filename)
                continue
            if (
                kind == "dependency"
                and directory
                and _default_native(dep)
                and path
                == "*/<multitarget_libc_selection>/<lib:" + dep.deliverable + ">"
            ):
                imports.setdefault(target, []).append(
                    "{primary}=" + dest + "{basename}"
                )
                continue
            if directory and (
                (
                    kind == "extracted-dependency"
                    and path is None
                    and type(dep).__name__ == "LayoutTARDistribution"
                )
                or (
                    kind == "dependency"
                    and path == "*"
                    and (
                        _source_project(dep)
                        or _native_tool_layout(dep)
                        or (
                            type(dep).__name__ == "LayoutDirDistribution"
                            and dep.isPlatformDependent()
                        )
                    )
                )
            ):
                if dest and not _resource_path(dest.rstrip("/")):
                    raise _UnsupportedLowering(
                        f"{dist.name}: invalid imported directory {dest}"
                    )
                imports.setdefault(target, []).append("=" + dest)
                continue
            if (
                _thin_launcher(dep)
                and kind == "dependency"
                and path
                == ("<os>-<arch>/<multitarget_libc_selection>/<exe:" + dep.name + ">")
            ):
                # Linux and macOS use the unadorned executable name.
                dest = re.sub(r"<exe:([A-Za-z0-9_.+-]+)>", r"\1", dest)
            elif not (
                kind == "dependency"
                and path is None
                and (
                    _language_library(dep)
                    or _native_image_executable(dep)
                    or _release_file(dep)
                    or _platform_file(dep)
                    or _thin_launcher(dep)
                )
            ):
                raise _UnsupportedLowering(
                    f"{dist.name}: unsupported install member {source['_str_']}"
                )
        if directory:
            if dest and not _resource_path(dest.rstrip("/")):
                raise _UnsupportedLowering(
                    f"{dist.name}: invalid install directory {dest}"
                )
            dest += "{basename}"
        elif not _resource_path(dest):
            raise _UnsupportedLowering(
                f"{dist.name}: invalid install destination {dest}"
            )
        members.setdefault(target, []).append(dest)
    if inputs != set(dist.buildDependencies) or not (members or imports):
        raise _UnsupportedLowering(
            f"{dist.name}: install inputs differ from its resolved build dependencies: {sorted(d.name for d in inputs ^ set(dist.buildDependencies))}"
        )
    return {
        **(
            {
                "jdk_homes": {
                    label: json.dumps(
                        homes[0] if len(homes) == 1 else homes, sort_keys=True
                    )
                    for label, homes in jdks.items()
                }
            }
            if jdks
            else {}
        ),
        **({"source_archives": source_archives} if source_archives else {}),
        "name": dist.name,
        "format": "dir",
        "testonly": dist.is_test_distribution(),
        "members": {key: "\n".join(paths) for key, paths in members.items()},
        "archive_imports": {key: "\n".join(paths) for key, paths in imports.items()},
    }


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
        raise _UnsupportedLowering(f"{dist.name}: unsupported resource layout")
    literals = {}
    copies = {}
    inputs = set()
    for dest, source in dist._walk_layout():
        if not _resource_path(dest) or dest in copies or dest in literals:
            raise _UnsupportedLowering(
                f"{dist.name}: expected a unique resource file path: {dest!r}"
            )
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
                raise _UnsupportedLowering(
                    f"{dist.name}: expected a file from a resource layout"
                )
            inputs.add(dep)
            if dep.suite == dist.suite:
                target = ":" + dep.name
                pending.append(dep)
            else:
                labels = mappings.get(dep.qualifiedName(), [])
                if len(labels) != 1:
                    raise _UnsupportedLowering(
                        f"{dist.name}: bind {dep.qualifiedName()} to a migrated resource layout"
                    )
                target = labels[0]
            copies[dest] = f"{target}.files/{source['path']}"
        else:
            raise _UnsupportedLowering(
                f"{dist.name}: unsupported resource layout source {source['_str_']}"
            )
    if not (literals or copies) or inputs != set(dist.buildDependencies):
        raise _UnsupportedLowering(
            f"{dist.name}: expected a nonempty file layout with explicit inputs"
        )
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
            raise _UnsupportedLowering(
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
    "[--all | project ...]",
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
        "--platform-library",
        action="append",
        default=[],
        metavar="SUITE:LIB",
        help="mapped library whose module name mx resolves for the host: depend on it without a requires directive",
    )
    parser.add_argument(
        "--suite-package",
        action="append",
        default=[],
        metavar="SUITE=//PACKAGE",
        help="resolve Java dependencies in an imported suite to its Bazel package",
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
        "--standalone-source-provenance", metavar="LABEL",
        help="source archive manifest for installed release metadata",
    )
    parser.add_argument(
        "--export-source-archive",
        action="append",
        default=[],
        metavar="DIST",
        help="publish a distribution source archive for imported suite layouts",
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
        "--test-data",
        action="append",
        default=[],
        metavar="SUITE:PROJECT=LABEL",
        help="declare a runtime file used by generated test classes",
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
        "--manual-test-class",
        action="append",
        default=[],
        metavar="CLASS",
        help="tag one generated JUnit class, by its fully qualified name, for explicit invocation",
    )
    parser.add_argument(
        "--patch-module-runtime",
        action="append",
        default=[],
        metavar="RELEASE=LABEL",
        help="JDK exposing the private APIs required by a Java module patch",
    )
    parser.add_argument(
        "--project-release",
        action="append",
        default=[],
        metavar="PROJECT=RELEASE",
        help="override the selected Java release for one project",
    )
    parser.add_argument(
        "--labs-javac-project",
        action="append",
        default=[],
        metavar="PROJECT",
        help="compile the selected project with the Labs JDK JavaBuilder",
    )
    parser.add_argument(
        "--empty-java-project",
        action="append",
        default=[],
        metavar="PROJECT",
        help="export an upstream Java project without sources as a dependency target",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="generate every project and distribution in the primary suite",
    )
    parser.add_argument(
        "--declared-only",
        action="store_true",
        help="limit --all roots to suite.py declarations; still export their resolved dependencies",
    )
    parser.add_argument(
        "--provided-target",
        action="append",
        default=[],
        metavar="NAME",
        help="existing Bazel target implementing a project or distribution in --all mode",
    )
    parser.add_argument(
        "--partial",
        action="store_true",
        help="export supported closures and report blocked suite roots",
    )
    parser.add_argument(
        "--wasm2wat", metavar="LABEL", help="WABT disassembler for compiled C fixtures"
    )
    parser.add_argument(
        "--wat2wasm", metavar="LABEL", help="WABT compiler for Wasm text projects"
    )
    parser.add_argument(
        "--sulong-fixture-sdk",
        metavar="LABEL",
        help="execution-platform Graal LLVM SDK for Sulong CMake test fixtures",
    )
    parser.add_argument(
        "--sulong-llvm-link",
        metavar="LABEL",
        help="execution-platform llvm-link for Sulong bitcode fixtures",
    )
    parser.add_argument(
        "--bindings-out",
        type=Path,
        help="write reusable Java bindings for emitted targets",
    )
    parser.add_argument(
        "--bindings",
        action="append",
        default=[],
        metavar="//PACKAGE=FILE",
        help="import the Java bindings published by another suite",
    )
    parser.add_argument(
        "--standalone-jdk-archive",
        metavar="LABEL",
        help="bind the tar archive of mx STANDALONE_JAVA_HOME",
    )
    parser.add_argument(
        "--layout-dep",
        action="append",
        default=[],
        metavar="SUITE:NAME=LABEL",
        help="bind an imported distribution that implements MxArchiveLayoutInfo",
    )
    parser.add_argument(
        "--standalone-jdk-release",
        metavar="LABEL",
        help="bind the release file from mx STANDALONE_JAVA_HOME",
    )
    parser.add_argument(
        "--svm-macro",
        action="append",
        default=[],
        metavar="COMPONENT",
        help="export the registered Native Image macro and its component dependencies",
    )
    parser.add_argument("projects", nargs="*")
    options = parser.parse_args(args)
    if options.all:
        if not options.gazelle or options.projects:
            parser.error("--all requires --gazelle and takes no project arguments")
        options.classpath_dists = True
    elif not options.projects or options.provided_target or options.declared_only:
        parser.error("select projects or use --all")
    if options.partial and not options.all:
        parser.error("--partial requires --all")
    if (options.bindings_out or options.bindings) and not options.gazelle:
        parser.error("Java bindings require --gazelle")
    bindings = _read_bindings(parser, options)
    try:
        selected = None
        blocked = {}
        if options.partial:
            suite = mx.primary_suite() or mx.suite("mx")
            selected = set()
            for root in _suite_candidates(options, suite):
                if root.name in selected:
                    continue
                if (
                    not _can_lower_root(root)
                    and root.name not in options.provided_target
                ):
                    reason = f"missing Bazel implementation for {type(root).__name__}"
                    blocked.setdefault(reason, []).append(root.qualifiedName())
                    continue
                if root.name in options.provided_target:
                    selected.add(root.name)
                    continue
                try:
                    closure = _lower_buildfile(
                        parser, options, bindings, {root.name}, probe=True
                    )
                except _UnsupportedLowering as error:
                    blocked.setdefault(str(error), []).append(root.qualifiedName())
                else:
                    selected.update(closure)
        result = _lower_buildfile(parser, options, bindings, selected)
        if options.gazelle:
            if options.svm_macro:
                result["svm_components"] = _svm_macro_components(options.svm_macro)
            if options.partial:
                result["blocked_roots"] = {k: sorted(v) for k, v in blocked.items()}
            if options.bindings_out:
                _write_bindings(options.bindings_out, result)
            print(json.dumps(result, indent=2, sort_keys=True))
    except _UnsupportedLowering as error:
        mx.abort(str(error))


def _suite_candidates(options, suite):
    candidates = suite.projects + suite.dists
    if options.declared_only:
        declarations = (
            suite.suiteDict.get("projects", {}).keys()
            | suite.suiteDict.get("distributions", {}).keys()
        )
        candidates = [dep for dep in candidates if getattr(dep, "declaration_name", dep.name) in declarations]
    return candidates


def _lower_buildfile(parser, options, bindings, selected, probe=False):
    empty_projects = set(options.empty_java_project)
    if empty_projects and not options.gazelle:
        parser.error("--empty-java-project requires --gazelle")
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
    for name in options.platform_library:
        if ":" not in name or name in library_modules:
            parser.error(f"expected one --platform-library SUITE:LIB for {name}")
        # None marks a module that the generated descriptor must not name.
        library_modules[name] = None
    test_launchers = _mappings(parser, options.test_launcher, "--test-launcher")
    test_runtimes = _mappings(parser, options.test_runtime, "--test-runtime")
    test_data = _mappings(parser, options.test_data, "--test-data")
    test_paths = _mappings(parser, options.test_path, "--test-path")
    used_test_paths = set()
    test_flags = {}
    for spec in options.test_jvm_flag:
        name, sep, value = spec.partition("=")
        if not sep or ":" not in name or not value.startswith("-"):
            parser.error("expected --test-jvm-flag SUITE:PROJECT=FLAG")
        test_flags.setdefault(name, []).append(value)
    manual_tests = set(options.manual_test_project)
    manual_classes = set(options.manual_test_class)
    labs_javac = set(options.labs_javac_project)
    if labs_javac and not options.gazelle:
        parser.error("--labs-javac-project requires --gazelle")
    patch_runtimes = {}
    for spec in options.patch_module_runtime:
        version, sep, label = spec.partition("=")
        if (
            not sep
            or not version.isdecimal()
            or not label.startswith(("@", "//"))
            or int(version) in patch_runtimes
        ):
            parser.error("expected a unique --patch-module-runtime RELEASE=LABEL")
        patch_runtimes[int(version)] = label
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
        test_runtimes
        or test_launchers
        or test_flags
        or manual_tests
        or test_data
        or manual_classes
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
    layout_deps = _mappings(parser, options.layout_dep, "--layout-dep")
    if options.suite_package and not options.classpath_dists:
        parser.error("--suite-package requires --classpath-dists or --all")
    for mapping in options.suite_package:
        name, sep, package = mapping.partition("=")
        if not sep or not re.fullmatch(
            r"(?:@[A-Za-z0-9_.+-]+)?//[A-Za-z0-9_./+-]*", package
        ):
            parser.error("expected --suite-package SUITE=//PACKAGE")
        imported = mx.suite(name)
        for dep in imported.projects + imported.dists:
            if (
                dep.isJavaProject()
                or dep.isJARDistribution()
                or type(dep).__name__ == "LayoutDirDistribution"
            ):
                java_deps.setdefault(dep.qualifiedName(), [f"{package}:{dep.name}"])
    for name, label in bindings["java"].items():
        java_deps.setdefault(name, [label])
    for name, labels in bindings["plugins"].items():
        mappings.setdefault(name, labels)
    provided = set()
    auto_test_runtimes = {}
    if options.all:
        suite = mx.primary_suite() or mx.suite("mx")
        provided = set(options.provided_target)
        candidates = _suite_candidates(options, suite)
        if unknown := provided - {dep.name for dep in candidates}:
            parser.error(f"unknown --provided-target names: {sorted(unknown)}")
        for name in provided:
            java_deps.setdefault(f"{suite.name}:{name}", [":" + name])
        roots = [dep for dep in candidates if dep.name not in provided]
        if selected is not None:
            roots = [dep for dep in roots if dep.name in selected]
        unsupported = sorted(
            f"{dep.qualifiedName()} ({type(dep).__name__})"
            for dep in roots
            if not _can_lower_root(dep)
        )
        if unsupported:
            raise _UnsupportedLowering(
                "Bazel lowerings required for these suite roots (bind existing rules with --provided-target):\n"
                + "\n".join(unsupported)
            )
        archives.update(dep.name for dep in candidates if dep.isJARDistribution())
        for dist in [] if probe else roots:
            if not (dist.isJARDistribution() and dist.is_test_distribution()):
                continue
            for project in dist.archived_deps():
                if (
                    project.suite == suite
                    and project.name not in provided
                    and project.isJavaProject()
                    and project.is_test_project()
                ):
                    auto_test_runtimes.setdefault(project.qualifiedName(), []).append(
                        ":" + dist.name
                    )
        for name, labels in auto_test_runtimes.items():
            if len(labels) == 1 and name not in test_launchers:
                test_runtimes.setdefault(name, labels)
    else:
        lookup = mx.dependency if options.classpath_dists else mx.project
        roots = [lookup(name) for name in options.projects]
        suite = roots[0].suite
    pending = list(roots)
    rules = {}
    plugins = {}
    graph = {}
    distributions = {}
    layouts = {}
    release_archives = {}
    tar_layouts = {}
    tool_layouts = {}
    bash_launchers = {}
    thin_launchers = {}
    image_projects = {}
    bitcode_suites = {}
    native_shared = {}
    source_projects = {}
    wasm_programs = {}
    wat_projects = {}
    cmake_suites = {}
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
            or project.name in release_archives
            or project.name in tar_layouts
            or project.name in tool_layouts
            or project.name in bash_launchers
            or project.name in thin_launchers
            or project.name in image_projects
            or project.name in bitcode_suites
            or project.name in native_shared
            or project.name in source_projects
            or project.name in wasm_programs
            or project.name in wat_projects
            or project.name in cmake_suites
        ):
            continue
        if options.classpath_dists and project.suite == suite:
            if _standalone_archive(project):
                release_archives[project.name] = _release_archive(project, pending)
                continue
            if _native_tool_layout(project):
                tool_layouts[project.name] = _tool_links(project, pending)
                continue
            if type(project).__name__ in ("LayoutDirDistribution", "LayoutTARDistribution") and {"os", "os_arch"} & getattr(project, "_orig_attrs", {}).keys():
                tar_layouts[project.name] = _os_layout(project, pending)
                continue
            if type(project).__name__ == "LayoutJARDistribution":
                tar_layouts[project.name] = _tar_layout(project, pending, java_deps, archives)
                continue
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
            if project.isPOMDistribution():
                distributions[project.name] = {
                    "name": project.name,
                    **_pom_edges(project, suite, java_deps, pending),
                    "pom": _genPom(
                        project, lambda owner: owner.release_version_from_suite()
                    ),
                    "pom_filename": project.maven_artifact_id() + ".pom",
                }
                continue
            if type(project).__name__ == "LayoutTARDistribution":
                tar_layouts[project.name] = _tar_layout(project, pending, java_deps, archives)
                continue
            if type(project).__name__ == "LayoutDirDistribution":
                if project.fileListEntry and project.hashEntry and project.isPlatformDependent():
                    tar_layouts[project.name] = _platform_native_layout(project, pending)
                    continue
                if project.isPlatformDependent() and options.standalone_jdk_release:
                    tar_layouts[project.name] = _install_layout(
                        project, pending, java_deps | layout_deps, options, bindings
                    )
                else:
                    layouts[project.name] = _resource_layout(
                        project, pending, java_deps
                    )
                continue
        if (
            options.gazelle
            and _in_tree_sulong_tests(project)
            and project.suite == suite
        ):
            try:
                bitcode_suites[project.name] = _bitcode_suite(
                    project, options.sulong_llvm_link
                )
            except _UnsupportedLowering:
                if not options.sulong_fixture_sdk:
                    raise
                cmake_suites[project.name] = _cmake_fixture_suite(
                    project, options.sulong_fixture_sdk
                )
            continue
        if (
            options.gazelle
            and project.suite == suite
            and (_native_image_executable(project) or _language_library(project))
        ):
            image_projects[project.name] = _image_recipe(
                project, suite, java_deps, pending
            )
            continue
        if options.gazelle and project.suite == suite and _bash_launcher(project):
            bash_launchers[project.name] = _bash_launcher_recipe(project, java_deps, pending, options.standalone_jdk_archive)
            continue
        if options.gazelle and project.suite == suite and _thin_launcher(project):
            thin_launchers[project.name] = _thin_launcher_recipe(project)
            continue
        if options.gazelle and project.suite == suite and _default_native(project):
            native_shared[project.name] = _native_shared_recipe(project, pending)
            continue
        if options.gazelle and project.suite == suite and _release_file(project):
            source_projects[project.name] = _release_file_recipe(project, options)
            continue
        if options.gazelle and project.suite == suite and _platform_file(project):
            source_projects[project.name] = _platform_file_recipe(project)
            continue
        if options.gazelle and project.suite == suite and _source_project(project):
            source_projects[project.name] = _project_files(project)
            continue
        if options.gazelle and project.suite == suite and _emscripten_project(project):
            wasm_programs[project.name] = _wasm_programs(project, options)
            continue
        if options.gazelle and project.suite == suite and _wat_project(project):
            if not options.wat2wasm:
                raise _UnsupportedLowering(
                    f"{project.name}: bind --wat2wasm to the WABT executable"
                )
            root = Path(project.getSourceDir())
            wat_projects[project.name] = {
                "name": project.name,
                "tool": options.wat2wasm,
                "root": root.relative_to(suite.dir).as_posix(),
                "cases": sorted(
                    (Path(d) / f).relative_to(root).as_posix()
                    for d, f in project.getProgramSources()
                ),
                "testonly": project.is_test_project(),
            }
            continue
        shaded = options.gazelle and _is_shaded_project(project)
        if (
            not shaded and type(project).__name__ != "JavaProject"
        ) or project.suite != suite:
            raise _UnsupportedLowering(
                f"{project.name}: requires a separate Bazel dependency mapping"
            )
        allowed = _PLAIN_JAVA
        # Preserve mx coverage exclusions as graph metadata.
        excluded_from_jacoco = project._orig_attrs.get("jacoco") == "exclude"
        if options.gazelle:
            # SpotBugs is a separate mx analysis pass.
            allowed = allowed | {"spotbugs", "spotbugsIgnoresGenerated", "javadocType", "eclipseformat", "skipVerifyImports", "checkstyleProj"}
            if getattr(project, "patchModule", None):
                allowed = allowed | {"patchModule"}
            if getattr(project, "multiReleaseJarVersion", None):
                allowed = allowed | {"multiReleaseJarVersion", "overlayTarget"}
        if options.gazelle and excluded_from_jacoco:
            allowed = allowed | {"jacoco"}
        if options.gazelle:
            # JavaProject.deps has the resolved compilation edges. Distribution
            # membership is resolved separately when exporting each archive.
            allowed = allowed | {
                "requiresConcealed",
                "generatedDependencies",
                "uses",
                "exports",
                "distDependencies",
            }
            if project._orig_attrs.get("jacoco") == "include":
                allowed = allowed | {
                    "jacoco",
                    "jacocoExcludePackages",
                    "jacocoExcludePackage",
                }
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
        property_data = (
            test_data.pop(project.qualifiedName(), [])
            if project.is_test_project()
            else []
        )
        if (
            options.gazelle
            and project.is_test_project()
            and project._orig_attrs.get("javaProperties")
        ):
            for key, value in project._orig_attrs["javaProperties"].items():
                library = _native_property_label(project, value)
                if library:
                    pending.append(library)
                    continue
                match = re.fullmatch(r"<path:([^>]+)>", value)
                dep = mx.dependency(match[1], context=project) if match else None
                labels = (
                    test_paths.get(
                        dep.qualifiedName(),
                        bindings["artifacts"].get(dep.qualifiedName(), []),
                    )
                    if dep
                    else []
                )
                if len(labels) != 1:
                    raise _UnsupportedLowering(
                        f"{project.name}: bind {key}={value} using --test-path"
                    )
                used_test_paths.add(dep.qualifiedName())
                property_data.append(labels[0])
                property_flags.append(f"-D{key}=$(rootpath {labels[0]})")
            allowed = allowed | {"javaProperties"}
        elif options.gazelle and not project.is_test_project():
            # mx applies these when assembling a runtime command line. javac and
            # JAR assembly do not substitute paths or consume the referenced files.
            allowed = allowed | {"javaProperties"}
        if options.gazelle:
            allowed = allowed | {"defaultBuild"}
        if shaded:
            allowed = allowed | {
                "class",
                "graalCompilerSourceEdition",
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
        force_javac = project._orig_attrs.get("forceJavac") in (True, "true")
        if options.gazelle and force_javac:
            allowed = allowed | {"forceJavac"}
        if project.name in labs_javac:
            labs_javac.remove(project.name)
            force_javac = True
        if project._orig_attrs.get("javac.lint.overrides") in (
            "-incubating",
            "-deprecation",
        ):
            allowed = allowed | {"javac.lint.overrides"}
        unsupported = sorted(set(project._orig_attrs) - allowed)
        if unsupported:
            raise _UnsupportedLowering(
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
            if not directory.is_relative_to(suite.dir):
                raise _UnsupportedLowering(
                    f"{project.name}: source root belongs to another suite or packed resource"
                )
            if shaded and not directory.exists():
                continue  # The suite may contain only generated sources.
            if not directory.is_dir():
                raise _UnsupportedLowering(
                    f"{project.name}: missing source root {directory}"
                )
            for source in sorted(directory.rglob("*")):
                if source.is_file() and source.name not in {"BUILD", "BUILD.bazel"}:
                    path = source.relative_to(suite.dir).as_posix()
                    if source.suffix == ".java":
                        sources.append(path)
                    else:
                        resources.append(path)
                        resource_roots.add(directory.relative_to(suite.dir).as_posix())
        if project.name in empty_projects:
            if sources or resources:
                mx.abort(f"{project.name}: project now contains sources or resources")
            empty_projects.remove(project.name)
            graph[project.name] = {
                "name": project.name,
                "release": project.javaCompliance.value,
                "empty": True,
                "testonly": project.is_test_project(),
                "deps": _dependency_labels(
                    project.deps, suite, java_deps, pending, options.classpath_dists
                ),
            }
            continue
        if not sources and not shaded:
            raise _UnsupportedLowering(f"{project.name}: no Java sources")
        deps = _dependency_labels(
            project.deps, suite, java_deps, pending, options.classpath_dists
        )
        deps.extend(generated_deps.pop(project.qualifiedName(), []))
        release = (
            project.javaCompliance.value if options.release is None else options.release
        )
        if shaded or getattr(project, "multiReleaseJarVersion", None):
            release = project.javaCompliance.value
        if options.partial and release not in project.javaCompliance:
            release = project.javaCompliance.value
        release = project_releases.pop(project.name, release)
        if release not in project.javaCompliance:
            raise _UnsupportedLowering(
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
            if lint in ("none", "-incubating", "-deprecation"):
                attrs["javac_opts"].append("-Xlint:" + lint)
        attrs["javac_opts"].extend(project_options.pop(project.name, []))
        if processors:
            processed = True
            attrs["plugins"] = processors
            attrs["javacopts"] = attrs.pop("javac_opts")
        else:
            attrs["unified_release"] = release
        if len(resource_roots) > 1 and (not options.gazelle or shaded):
            raise _UnsupportedLowering(
                f"{project.name}: resources under multiple roots require separate packaging"
            )
        resource_attr = ""
        if resources:
            has_resources = True
            attrs["resources"] = sorted(resources)
            if len(resource_roots) == 1:
                resource_root = next(iter(resource_roots))
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
            jvmci = "jdk.internal.vm.ci" in concealed or "jdk.internal.vm.ci" in (
                project._orig_attrs.get("requires", [])
            )
            coarse = bool(
                jvmci
                or shaded
                or getattr(project, "patchModule", None)
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
            if module := getattr(project, "patchModule", None):
                if (
                    release not in (17, 21, 25)
                    or resources
                    or processors
                    or concealed
                    or jni_headers
                    or any(
                        getattr(d, "patchModule", None) != module for d in project.deps
                    )
                ):
                    raise _UnsupportedLowering(
                        f"{project.name}: unsupported toolchain or dependency for patching {module}"
                    )
                record["patch_module"] = module
                record["patch_runtime"] = patch_runtimes.get(
                    release, f"@rules_java//toolchains:remotejdk_{release}"
                )
            if props := project._orig_attrs.get("javaProperties"):
                record["runtime_property_templates"] = dict(props)
            if shaded and not record["dirs"]:
                record["dirs"] = record["roots"]
            if jvmci:
                record["jvmci"] = True
            if jni_headers:
                record["jni_headers"] = True
            if force_javac:
                record["force_javac"] = True
            if concealed:
                record["add_exports"] = [
                    f"{mod}/{pkg}" for mod, pkgs in concealed.items() for pkg in pkgs
                ]
            if concealed or jvmci or "patch_module" in record:
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
                        raise _UnsupportedLowering(
                            f"{project.name}: bind the binary and sources of {lib.qualifiedName()} with --shaded-input"
                        )
                    inputs.extend(labels)
                record["shade_inputs"] = inputs
            if "spotbugs" in project._orig_attrs:
                record["spotbugs"] = project._orig_attrs["spotbugs"]
            if jacoco := project._orig_attrs.get("jacoco"):
                record["jacoco"] = jacoco
            if exclusions := project._orig_attrs.get(
                "jacocoExcludePackages", project._orig_attrs.get("jacocoExcludePackage")
            ):
                record["jacoco_exclude_packages"] = exclusions
            if coarse:
                record["sources"] = sorted(sources)
                if resources:
                    record["resources"] = sorted(resources)
                    # Graal’s option processor reads documentation from javac’s source path.
                    needs_source_resources = processors and any(
                        "/doc-files/" in path for path in resources
                    )
                    if len(resource_roots) > 1 or (
                        needs_source_resources and not shaded
                    ):
                        record["resource_roots"] = sorted(resource_roots)
                        record["force_javac"] = True
                    else:
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
                            raise _UnsupportedLowering(
                                f"{project.name}: --test-launcher supplies the entire test runtime"
                            )
                        record["test_launcher"] = launchers[0]
                    if project.name in manual_tests:
                        manual_tests.remove(project.name)
                        record["manual_tests"] = True
                    if manual := sorted(manual_classes & set(record["tests"])):
                        manual_classes.difference_update(manual)
                        record["manual_test_classes"] = manual
                    if test_runtime:
                        record["test_runtime"] = test_runtime
                    for owner in mx.classpath_entries([project]):
                        for key, value in getattr(owner, "_orig_attrs", {}).get("javaProperties", {}).items():
                            library = _native_property_label(owner, value)
                            if library and library.suite == project.suite:
                                pending.append(library)
                                target = ":" + library.name + ".library"
                                property_data.append(target)
                                property_flags.append(f"-D{key}=$${{TEST_SRCDIR}}/$(rlocationpath {target})")
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
    if probe:
        return (
            graph.keys()
            | rules.keys()
            | layouts.keys()
            | distributions.keys()
            | tool_layouts.keys()
            | bash_launchers.keys()
            | thin_launchers.keys()
            | image_projects.keys()
            | native_shared.keys()
            | source_projects.keys()
            | wasm_programs.keys()
            | wat_projects.keys()
            | cmake_suites.keys()
            | bitcode_suites.keys()
            | tar_layouts.keys()
            | release_archives.keys()
        )
    if (
        unused_projects := project_releases.keys()
        | manual_tests
        | manual_classes
        | labs_javac
        | empty_projects
    ):
        mx.abort(f"Unused project configuration: {sorted(unused_projects)}")
    if unused_paths := test_paths.keys() - used_test_paths:
        mx.abort(f"Unused test path mappings: {sorted(unused_paths)}")
    if test_launchers or test_runtimes or test_flags or test_data:
        mx.abort(
            f"Unused test configuration: {sorted(test_runtimes.keys() | test_flags.keys() | test_launchers.keys() | test_data.keys())}"
        )
    if generated_deps:
        mx.abort(f"Unknown generated dependency project: {sorted(generated_deps)}")
    if project_options:
        mx.abort(f"Unknown javac option project: {sorted(project_options)}")
    if rules.keys() & plugins.keys():
        mx.abort("A generated plugin target conflicts with a project name")
    if options.gazelle:
        if (
            missing_archives := (
                set(options.archive_dist) if options.partial else archives
            )
            - distributions.keys()
            - provided
        ):
            mx.abort(f"Unknown archive in selected closure: {sorted(missing_archives)}")
        unmatched = javac_only_processors - used_javac_only
        if unmatched:
            mx.abort(
                f"Unknown javac-only processor in selected closure: {sorted(unmatched)}"
            )
        missing = set(options.whole_project) - graph.keys()
        if missing:
            mx.abort(f"Unknown whole project in selected closure: {sorted(missing)}")
        if options.infer_source_deps or options.bindings:
            _source_dependencies(
                graph, suite, java_deps, distributions, bindings["packages"]
            )
            _module_reads_from_projects(graph, suite, java_deps, distributions)
        result = {
            "version": 1,
            "projects": [graph[n] for n in sorted(graph)],
            "plugins": [plugins[n] for n in sorted(plugins)],
        }
        if options.all:
            if selected is not None:
                candidates = [dep for dep in candidates if dep.name in selected]
            aliases = {
                d.declaration_name: d.name
                for d in candidates
                if getattr(d, "declaration_name", d.name) != d.name
            }
            if aliases:
                result["distribution_aliases"] = aliases
            result["suite_targets"] = {
                "projects": sorted(dep.name for dep in candidates if dep.isProject()),
                "distributions": sorted(
                    getattr(dep, "declaration_name", dep.name)
                    for dep in candidates
                    if dep.isDistribution()
                ),
            }
        if distributions:
            result["distributions"] = [distributions[n] for n in sorted(distributions)]
        if layouts:
            result["layouts"] = [layouts[n] for n in sorted(layouts)]
        if tool_layouts:
            result["tool_layouts"] = [tool_layouts[n] for n in sorted(tool_layouts)]
        if bash_launchers:
            result["bash_launchers"] = [bash_launchers[n] for n in sorted(bash_launchers)]
        if thin_launchers:
            result["thin_launchers"] = [thin_launchers[n] for n in sorted(thin_launchers)]
        if image_projects:
            result["image_projects"] = [
                image_projects[n] for n in sorted(image_projects)
            ]
        if options.export_source_archive:
            result["source_exports"] = {name: _source_archive_groups(suite.dependency(name)) for name in sorted(options.export_source_archive)}
        if native_shared:
            result["native_shared"] = [native_shared[n] for n in sorted(native_shared)]
        if source_projects:
            result["source_projects"] = [
                source_projects[n] for n in sorted(source_projects)
            ]
        if wasm_programs:
            result["wasm_programs"] = [wasm_programs[n] for n in sorted(wasm_programs)]
        if wat_projects:
            result["wat_projects"] = [wat_projects[n] for n in sorted(wat_projects)]
        if cmake_suites:
            result["cmake_suites"] = [cmake_suites[n] for n in sorted(cmake_suites)]
        if bitcode_suites:
            result["bitcode_suites"] = [
                bitcode_suites[n] for n in sorted(bitcode_suites)
            ]
        if release_archives:
            result["release_archives"] = [
                release_archives[n] for n in sorted(release_archives)
            ]
        if tar_layouts:
            result["tar_layouts"] = [tar_layouts[n] for n in sorted(tar_layouts)]
        return result
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
