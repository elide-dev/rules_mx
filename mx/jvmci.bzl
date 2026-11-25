"""Compile projects using the Labs JDK's concealed JVMCI APIs with JavaBuilder."""

load("@rules_java//java/common:java_common.bzl", "java_common")
load("@rules_java//java/common:java_info.bzl", "JavaInfo")
load("@rules_java//java/common:java_plugin_info.bzl", "JavaPluginInfo")

def _jvmci_impl(ctx):
    jar = ctx.actions.declare_file(ctx.label.name + ".jar")
    toolchain = ctx.attr._labs[java_common.JavaToolchainInfo]
    modules = sorted({item.split("/")[0]: True for item in ctx.attr.add_exports})
    info = java_common.compile(
        ctx,
        output = jar,
        java_toolchain = toolchain,
        source_files = ctx.files.srcs,
        resources = ctx.files.resources,
        javac_opts = ctx.attr.javacopts + (["--add-modules=" + ",".join(modules)] if modules else []) + [
            "--add-exports=" + item + "=ALL-UNNAMED"
            for item in ctx.attr.add_exports
        ],
        plugins = [dep[JavaPluginInfo] for dep in ctx.attr.plugins],
        deps = [dep[JavaInfo] for dep in ctx.attr.deps],
    )
    return [DefaultInfo(files = depset([jar])), info]

_jvmci_library = rule(
    implementation = _jvmci_impl,
    attrs = {
        "srcs": attr.label_list(allow_files = [".java"]),
        "resources": attr.label_list(allow_files = True),
        "resource_strip_prefix": attr.string(),
        "javacopts": attr.string_list(),
        "add_exports": attr.string_list(),
        "deps": attr.label_list(providers = [JavaInfo]),
        "plugins": attr.label_list(providers = [JavaPluginInfo], cfg = "exec"),
        "_labs": attr.label(default = Label("//tools:labs_jvmci"), cfg = "exec"),
    },
    fragments = ["java"],
    toolchains = ["@bazel_tools//tools/jdk:toolchain_type"],
)

def mx_jvmci_library(name, mx_resource_root = None, **kwargs):
    _jvmci_library(
        name = name,
        resource_strip_prefix = native.package_name() + "/" + mx_resource_root if mx_resource_root else None,
        **kwargs
    )
