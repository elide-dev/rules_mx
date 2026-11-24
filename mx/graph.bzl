"""Rules emitted by the mx Gazelle graph importer."""

load("@rules_java//java:java_library.bzl", "java_library")
load("@rules_java//java:java_plugin.bzl", "java_plugin")
load("@rules_java//java:java_test.bzl", "java_test")
load("@rules_java//java/common:java_common.bzl", "java_common")
load("@rules_java//java/common:java_info.bzl", "JavaInfo")
load(":java.bzl", "elide_java_library", "mx_resource_prefix")

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

def mx_whole_java(mx_resource_root = None, **kwargs):
    if mx_resource_root:
        kwargs["resource_strip_prefix"] = mx_resource_prefix(mx_resource_root)
    elide_java_library(**kwargs)

def mx_processed_java(mx_resource_root = None, **kwargs):
    if mx_resource_root:
        kwargs["resource_strip_prefix"] = mx_resource_prefix(mx_resource_root, repo_relative = True)
    java_library(**kwargs)

def mx_test_class(name, project, test_class):
    """Run one mx JUnit 4 test class."""
    java_test(name = name, runtime_deps = [project], test_class = test_class)
