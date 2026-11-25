"""Rules emitted by the mx Gazelle graph importer."""

load("@rules_java//java:java_import.bzl", "java_import")
load("@rules_java//java:java_library.bzl", "java_library")
load("@rules_java//java:java_plugin.bzl", "java_plugin")
load("@rules_java//java:java_test.bzl", "java_test")
load("@rules_java//java/common:java_common.bzl", "java_common")
load("@rules_java//java/common:java_info.bzl", "JavaInfo")
load(":jar.bzl", _mx_jar_dist = "mx_jar_dist")
load(":java.bzl", "elide_java_library", "mx_resource_prefix")
load(":jvmci.bzl", _mx_jvmci_library = "mx_jvmci_library")
load(":shade.bzl", _mx_shaded_java = "mx_shaded_java")

mx_jvmci_library = _mx_jvmci_library

mx_shaded_java = _mx_shaded_java

mx_jar_dist = _mx_jar_dist

mx_processor = java_plugin

mx_files = native.filegroup

def _mx_project_impl(ctx):
    members = ctx.attr.packages + ctx.attr.deps
    return [
        java_common.merge([member[JavaInfo] for member in members]),
        coverage_common.instrumented_files_info(ctx, dependency_attributes = ["packages", "deps"]),
        DefaultInfo(
            files = depset(transitive = [pkg[DefaultInfo].files for pkg in ctx.attr.packages]),
            runfiles = ctx.runfiles().merge_all([member[DefaultInfo].default_runfiles for member in members]),
        ),
    ]

mx_project = rule(
    implementation = _mx_project_impl,
    doc = "Build the package jars of an mx project and expose their combined JavaInfo.",
    attrs = {
        "packages": attr.label_list(providers = [JavaInfo], mandatory = True),
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

def mx_processed_java(name, mx_resource_root = None, abi_after_javac = False, deps = [], testonly = False, visibility = None, **kwargs):
    if mx_resource_root:
        kwargs["resource_strip_prefix"] = mx_resource_prefix(mx_resource_root, repo_relative = True)
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

def mx_test_class(name, project, test_class):
    """Run one mx JUnit 4 test class."""
    java_test(name = name, runtime_deps = [project], test_class = test_class)

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
