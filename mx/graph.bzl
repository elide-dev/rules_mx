"""Rules emitted by the mx Gazelle graph importer."""

load("@rules_java//java:java_import.bzl", "java_import")
load("@rules_java//java:java_library.bzl", "java_library")
load("@rules_java//java:java_plugin.bzl", "java_plugin")
load("@rules_java//java:java_test.bzl", "java_test")
load("@rules_java//java/common:java_common.bzl", "java_common")
load("@rules_java//java/common:java_info.bzl", "JavaInfo")
load("//graal:layout.bzl", "mx_archive_layout")
load(":jar.bzl", _mx_jar_dist = "mx_jar_dist")
load(":java.bzl", "elide_java_library", "mx_resource_prefix")
load(":jvmci.bzl", _mx_jvmci_library = "mx_jvmci_library")
load(":shade.bzl", _mx_shaded_java = "mx_shaded_java")

mx_shaded_java = _mx_shaded_java

mx_jar_dist = _mx_jar_dist

mx_processor = java_plugin

mx_exported_tar = mx_archive_layout

mx_files = native.filegroup

def _mx_project_impl(ctx):
    members = ctx.attr.packages + ctx.attr.deps
    return [
        java_common.merge([member[JavaInfo] for member in members]),
        coverage_common.instrumented_files_info(ctx, dependency_attributes = ["packages", "deps"]),
        DefaultInfo(
            files = depset(transitive = [pkg[DefaultInfo].files for pkg in (ctx.attr.packages or ctx.attr.deps)]),
            runfiles = ctx.runfiles().merge_all([member[DefaultInfo].default_runfiles for member in members]),
        ),
    ]

mx_project = rule(
    implementation = _mx_project_impl,
    doc = "Build the package jars of an mx project and expose their combined JavaInfo.",
    attrs = {
        "packages": attr.label_list(providers = [JavaInfo]),
        "deps": attr.label_list(providers = [JavaInfo]),
    },
    provides = [JavaInfo],
)

def mx_classpath_dist(name, deps, **kwargs):
    """Expose a distribution's constituent jars and their combined JavaInfo."""
    mx_project(name = name, packages = deps, **kwargs)

def mx_whole_java(mx_resource_root = None, **kwargs):
    if mx_resource_root:
        kwargs["resource_strip_prefix"] = mx_resource_prefix(mx_resource_root)
    elide_java_library(**kwargs)

def _java_headers_impl(ctx):
    (output,) = ctx.attr.project[JavaInfo].java_outputs
    if not output.native_headers_jar:
        fail("Java compiler did not expose JNI headers for " + str(ctx.attr.project.label))
    return [DefaultInfo(files = depset([output.native_headers_jar]))]

_java_headers = rule(
    implementation = _java_headers_impl,
    attrs = {"project": attr.label(providers = [JavaInfo], mandatory = True)},
)

def mx_jvmci_library(name, mx_resource_root = None, jni_headers = False, testonly = False, visibility = None, **kwargs):
    _mx_jvmci_library(name = name, mx_resource_root = mx_resource_root, testonly = testonly, visibility = visibility, **kwargs)
    if jni_headers:
        _java_headers(name = name + ".jni_headers", project = ":" + name, testonly = testonly, visibility = visibility)

def mx_processed_java(name, mx_resource_root = None, abi_after_javac = False, deps = [], testonly = False, visibility = None, jni_headers = False, **kwargs):
    if mx_resource_root:
        kwargs["resource_strip_prefix"] = mx_resource_prefix(mx_resource_root, repo_relative = True)
    if jni_headers:
        _java_headers(name = name + ".jni_headers", project = ":" + (name + ".javac" if abi_after_javac else name), testonly = testonly, visibility = visibility)
    if not abi_after_javac:
        java_library(name = name, deps = deps, testonly = testonly, visibility = visibility, **kwargs)
        return

    # Some processors inspect javac internals and cannot execute inside Turbine.
    # Import the processed class jar so downstream actions use its generated ABI.
    compiled = name + ".javac"
    java_library(name = compiled, deps = deps, testonly = testonly, visibility = ["//visibility:private"], **kwargs)
    java_import(
        name = name,
        jars = [":lib" + compiled + ".jar"],
        add_exports = kwargs.get("add_exports", []),
        srcjar = ":lib" + compiled + "-src.jar",
        deps = deps,
        testonly = testonly,
        visibility = visibility,
    )

def _launcher_test_impl(ctx):
    exe = ctx.actions.declare_file(ctx.label.name)
    ctx.actions.symlink(output = exe, target_file = ctx.executable.launcher, is_executable = True)
    return [
        DefaultInfo(executable = exe, runfiles = ctx.attr.launcher[DefaultInfo].default_runfiles),
        RunEnvironmentInfo(environment = {"MX_TEST_CLASS": ctx.attr.test_class}),
    ]

_launcher_test = rule(
    implementation = _launcher_test_impl,
    attrs = {
        "launcher": attr.label(mandatory = True, executable = True, cfg = "target"),
        "test_class": attr.string(mandatory = True),
    },
    test = True,
)

def mx_test_class(name, project = None, test_class = None, runtime_deps = [], jvm_flags = [], tags = [], launcher = None, data = []):
    """Run one mx JUnit 4 test class."""
    if launcher:
        _launcher_test(name = name, launcher = launcher, test_class = test_class, tags = tags, size = "medium")
        return
    if not test_class or not (project or runtime_deps):
        fail("mx_test_class requires test_class and a project or runtime_deps")
    java_test(name = name, data = data, runtime_deps = ([project] if project else []) + runtime_deps, test_class = test_class, jvm_flags = jvm_flags, tags = tags)

def _layout_file_impl(ctx):
    if ctx.file.src:
        ctx.actions.symlink(output = ctx.outputs.out, target_file = ctx.file.src)
    else:
        ctx.actions.write(ctx.outputs.out, ctx.attr.content)

_layout_file = rule(
    implementation = _layout_file_impl,
    attrs = {
        "out": attr.output(mandatory = True),
        "content": attr.string(),
        "src": attr.label(allow_single_file = True),
    },
)

def mx_resource_layout(name, literals = {}, copies = {}, testonly = False, **kwargs):
    """Expose exact mx layout files as a resource jar and individual outputs."""
    root = name + ".files"
    resources = []
    for path in sorted(literals.keys() + copies.keys()):
        out = root + "/" + path
        _layout_file(
            name = name + ".write/" + path,
            out = out,
            content = literals.get(path, ""),
            src = copies.get(path),
            testonly = testonly,
            visibility = kwargs.get("visibility"),
        )
        resources.append(out)
    java_library(
        name = name,
        resources = resources,
        resource_strip_prefix = mx_resource_prefix(root, repo_relative = True),
        testonly = testonly,
        **kwargs
    )

def _pom_dist_impl(ctx):
    ctx.actions.write(ctx.outputs.out, ctx.attr.content)
    deps = ctx.attr.deps
    return [
        DefaultInfo(
            files = depset([ctx.outputs.out]),
            runfiles = ctx.runfiles(transitive_files = depset(transitive = [dep.files for dep in deps])).merge_all([dep[DefaultInfo].default_runfiles for dep in deps]),
        ),
        java_common.merge([dep[JavaInfo] for dep in deps]),
    ]

_pom_dist = rule(
    implementation = _pom_dist_impl,
    attrs = {
        "content": attr.string(mandatory = True),
        "deps": attr.label_list(providers = [JavaInfo]),
        "out": attr.output(mandatory = True),
    },
)

def mx_pom_dist(name, out, content, deps = [], runtime_deps = [], **kwargs):
    """Expose the Maven descriptor and preserve its compile and runtime classpaths."""
    if runtime_deps:
        runtime = name + ".runtime"
        java_library(
            name = runtime,
            runtime_deps = runtime_deps,
            testonly = kwargs.get("testonly", False),
            visibility = ["//visibility:private"],
        )
        deps = deps + [":" + runtime]
    _pom_dist(name = name, out = out, content = content, deps = deps, **kwargs)
