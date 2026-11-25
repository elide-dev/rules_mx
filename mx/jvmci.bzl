"""Compile projects using the Labs JDK's concealed JVMCI APIs with JavaBuilder."""

load("@rules_java//java/common:java_common.bzl", "java_common")
load("@rules_java//java/common:java_info.bzl", "JavaInfo")
load("@rules_java//java/common:java_plugin_info.bzl", "JavaPluginInfo")
load(":java.bzl", "mx_resource_prefix")

def _jvmci_impl(ctx):
    jar = ctx.actions.declare_file(ctx.label.name + ".jar")
    toolchain = ctx.attr._labs[java_common.JavaToolchainInfo]
    modules = sorted({item.split("/")[0]: True for item in ctx.attr.add_exports})
    resources = ctx.files.resources
    resource_jar = None
    source_tree = None
    classes = jar
    deps = [dep[JavaInfo] for dep in ctx.attr.deps]
    if ctx.attr.resource_roots:
        resource_jar = ctx.actions.declare_file(ctx.label.name + ".resources.jar")

        # Javac’s Filer needs a directory on the source path to load option help.
        source_tree = ctx.actions.declare_directory(ctx.label.name + ".resource_sources")
        args = ctx.actions.args()
        args.add_all([resource_jar, "--source-tree", source_tree], expand_directories = False)
        members = {}
        for resource in resources:
            matches = [root for root in ctx.attr.resource_roots if resource.short_path.startswith(root + "/")]
            if len(matches) != 1:
                fail("Expected one resource root for " + resource.short_path)
            member = resource.short_path[len(matches[0]) + 1:]
            if member in members:
                fail("Duplicate resource " + member)
            members[member] = resource
        for member in sorted(members):
            args.add(member)
            args.add(members[member])
        ctx.actions.run(
            executable = ctx.executable._pack_layout,
            arguments = [args],
            inputs = resources,
            outputs = [resource_jar, source_tree],
            mnemonic = "MxJavaResources",
        )
        classes = ctx.actions.declare_file(ctx.label.name + ".classes.jar")
        resources = []
    info = java_common.compile(
        ctx,
        output = classes,
        java_toolchain = toolchain,
        source_files = ctx.files.srcs,
        resources = resources,
        sourcepath = [source_tree] if source_tree else [],
        javac_opts = ctx.attr.javacopts + (["--add-modules=" + ",".join(modules)] if modules else []) + [
            "--add-exports=" + item + "=ALL-UNNAMED"
            for item in ctx.attr.add_exports
        ],
        plugins = [dep[JavaPluginInfo] for dep in ctx.attr.plugins],
        deps = deps,
    )
    if resource_jar:
        args = ctx.actions.args()
        args.add("--output", jar)
        args.add_all([classes, resource_jar], before_each = "--input")
        ctx.actions.run(
            executable = ctx.executable._pack_jar,
            arguments = [args],
            inputs = [classes, resource_jar],
            outputs = [jar],
            mnemonic = "MxJavaResourceJar",
        )
        [java_output] = info.java_outputs
        source_jars = java_output.source_jars.to_list()
        info = JavaInfo(
            output_jar = jar,
            compile_jar = java_output.compile_jar,
            source_jar = source_jars[0] if source_jars else None,
            native_headers_jar = java_output.native_headers_jar,
            deps = deps,
        )
    return [DefaultInfo(files = depset([jar])), info]

_jvmci_library = rule(
    implementation = _jvmci_impl,
    attrs = {
        "srcs": attr.label_list(allow_files = [".java"]),
        "resources": attr.label_list(allow_files = True),
        "resource_strip_prefix": attr.string(),
        "resource_roots": attr.string_list(),
        "_pack_jar": attr.label(default = Label("//mx:pack_jar"), executable = True, cfg = "exec"),
        "_pack_layout": attr.label(default = Label("//mx:pack_layout"), executable = True, cfg = "exec"),
        "javacopts": attr.string_list(),
        "add_exports": attr.string_list(),
        "deps": attr.label_list(providers = [JavaInfo]),
        "plugins": attr.label_list(providers = [JavaPluginInfo], cfg = "exec"),
        "_labs": attr.label(default = Label("//tools:labs_jvmci"), cfg = "exec"),
    },
    fragments = ["java"],
    toolchains = ["@bazel_tools//tools/jdk:toolchain_type"],
)

def mx_jvmci_library(name, mx_resource_root = None, mx_resource_roots = [], **kwargs):
    _jvmci_library(
        name = name,
        resource_roots = [mx_resource_prefix(root) for root in mx_resource_roots],
        resource_strip_prefix = native.package_name() + "/" + mx_resource_root if mx_resource_root else None,
        **kwargs
    )
