"""Rules emitted by the mx Gazelle graph importer."""

load("@rules_java//java:java_library.bzl", "java_library")
load("@rules_java//java:java_plugin.bzl", "java_plugin")
load("@rules_java//java:java_test.bzl", "java_test")
load(":java.bzl", "elide_java_library", "mx_resource_prefix")

mx_processor = java_plugin

mx_files = native.filegroup

def mx_project(name, packages, deps = [], **kwargs):
    """Expose the JavaInfo of an mx project, including its declared dependencies."""
    java_library(name = name, exports = packages + deps, **kwargs)

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
