"""Package the pinned mx disassembler library for its target platform."""

def _hsdis_impl(ctx):
    pin = json.decode(ctx.read(ctx.attr.lock))[ctx.attr.platform]
    ctx.download_and_extract(pin["url"], output = "archive", integrity = pin["integrity"])
    members = ctx.path("archive").readdir()
    if len(members) != 1 or not members[0].basename.endswith("." + pin["output"].split(".")[-1]):
        fail("Unexpected HSDIS archive layout for " + ctx.attr.platform)
    resource = "resources/" + pin["output"]
    ctx.symlink(members[0], resource)
    ctx.file("BUILD.bazel", """
load("@rules_java//java:java_library.bzl", "java_library")
filegroup(name = "library", srcs = [%r], visibility = ["//visibility:public"])
java_library(
    name = "jar",
    resources = [%r],
    resource_strip_prefix = "resources",
    visibility = ["//visibility:public"],
)
""" % (resource, resource))

hsdis = repository_rule(
    implementation = _hsdis_impl,
    attrs = {"platform": attr.string(mandatory = True), "lock": attr.label(mandatory = True)},
)
