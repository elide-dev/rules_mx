"""Name a library JAR as the automatic module that mx's module descriptors require."""

load("@rules_java//java/common:java_common.bzl", "java_common")
load("@rules_java//java/common:java_info.bzl", "JavaInfo")

_TOOLCHAIN = "@bazel_tools//tools/jdk:toolchain_type"

def _automatic_module_impl(ctx):
    [source] = ctx.attr.jar[JavaInfo].runtime_output_jars
    jar = ctx.actions.declare_file(ctx.label.name + "/" + ctx.attr.module_name + ".jar")
    ctx.actions.run(
        executable = ctx.executable._rename,
        arguments = [source.path, jar.path, ctx.attr.module_name],
        inputs = [source],
        outputs = [jar],
        mnemonic = "AutomaticModule",
    )
    deps = [dep[JavaInfo] for dep in ctx.attr.deps]
    abi = java_common.run_ijar(ctx.actions, jar, target_label = ctx.label, java_toolchain = ctx.toolchains[_TOOLCHAIN].java)
    return [
        DefaultInfo(files = depset([jar])),
        JavaInfo(output_jar = jar, compile_jar = abi, deps = deps, exports = deps),
    ]

automatic_module = rule(
    implementation = _automatic_module_impl,
    doc = """Copies the runtime JAR of `jar` with an Automatic-Module-Name manifest entry.

    The copy replaces the library in module paths: `deps` are its only transitive JARs, so
    the original library's dependencies do not appear under a second module name.""",
    attrs = {
        "jar": attr.label(mandatory = True, providers = [JavaInfo]),
        "module_name": attr.string(mandatory = True),
        "deps": attr.label_list(providers = [JavaInfo]),
        "_rename": attr.label(default = Label("//graal:automatic_module"), executable = True, cfg = "exec"),
    },
    toolchains = [_TOOLCHAIN],
)
