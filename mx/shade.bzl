"""Compile a shaded Java project from pinned source and resource archives."""

load("@rules_java//java/common:java_common.bzl", "java_common")
load("@rules_java//java/common:java_info.bzl", "JavaInfo")
load(":java.bzl", "mx_resource_prefix")

_JAVA = "@bazel_tools//tools/jdk:toolchain_type"

def _shade_impl(ctx):
    srcjar = ctx.actions.declare_file(ctx.label.name + "-shaded.srcjar")
    resources = ctx.actions.declare_file(ctx.label.name + "-resources.jar")
    spec = ctx.actions.declare_file(ctx.label.name + "-shade.json")
    ctx.actions.write(spec, ctx.attr.shade)
    args = ctx.actions.args()
    args.add("--spec", spec)
    args.add("--release", ctx.attr.release)
    args.add("--srcjar", srcjar)
    args.add("--resources", resources)
    args.add_all(ctx.files.shade_inputs, before_each = "--input")
    for resource in ctx.files.resources:
        prefix = ctx.attr.resource_prefix + "/"
        if not resource.short_path.startswith(prefix):
            fail("Resource outside declared root: " + resource.short_path)
        args.add("--resource")
        args.add(resource.short_path[len(prefix):])
        args.add(resource)
    ctx.actions.run(
        executable = ctx.executable._shader,
        arguments = [args],
        inputs = [spec] + ctx.files.shade_inputs + ctx.files.resources,
        outputs = [srcjar, resources],
        mnemonic = "MxShade",
    )
    jar = ctx.actions.declare_file(ctx.label.name + ".jar")
    classes = ctx.actions.declare_file(ctx.label.name + "-classes.jar")
    deps = [d[JavaInfo] for d in ctx.attr.deps]
    info = java_common.compile(
        ctx,
        java_toolchain = ctx.toolchains[_JAVA].java,
        source_files = ctx.files.srcs,
        source_jars = [srcjar],
        javac_opts = ctx.attr.javacopts,
        deps = deps,
        output = classes,
    )
    args = ctx.actions.args()
    args.add("--output", jar)
    args.add_all([classes, resources], before_each = "--input")
    ctx.actions.run(
        executable = ctx.executable._packer,
        inputs = [classes, resources],
        outputs = [jar],
        arguments = [args],
        mnemonic = "MxShadeJar",
    )
    return [DefaultInfo(files = depset([jar])), JavaInfo(output_jar = jar, compile_jar = info.java_outputs[0].compile_jar, deps = deps)]

_shaded = rule(
    implementation = _shade_impl,
    attrs = {
        "shade": attr.string(mandatory = True),
        "release": attr.int(mandatory = True),
        "shade_inputs": attr.label_list(allow_files = [".jar"]),
        "srcs": attr.label_list(allow_files = [".java"]),
        "resources": attr.label_list(allow_files = True),
        "resource_prefix": attr.string(),
        "deps": attr.label_list(providers = [JavaInfo]),
        "javacopts": attr.string_list(),
        "_packer": attr.label(default = Label("//mx:pack_jar"), executable = True, cfg = "exec"),
        "_shader": attr.label(default = Label("//mx:shade"), executable = True, cfg = "exec"),
    },
    fragments = ["java"],
    toolchains = [_JAVA],
    provides = [JavaInfo],
)

def mx_shaded_java(mx_resource_root = None, **kwargs):
    _shaded(resource_prefix = mx_resource_prefix(mx_resource_root) if mx_resource_root else "", **kwargs)
