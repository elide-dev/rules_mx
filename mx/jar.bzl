"""Build a Java distribution from the archive members resolved by mx."""

load("@rules_java//java/common:java_common.bzl", "java_common")
load("@rules_java//java/common:java_info.bzl", "JavaInfo")

MxJarInfo = provider(fields = ["uses_jvmci", "required_exports"])

_TOOLCHAIN = "@bazel_tools//tools/jdk:toolchain_type"

def _jar_impl(ctx):
    toolchain = ctx.toolchains[_TOOLCHAIN].java
    jar = ctx.actions.declare_file(ctx.label.name + ".jar")
    dependencies = [dep[JavaInfo] for dep in ctx.attr.deps]
    inputs = depset(transitive = [dep.files for dep in ctx.attr.contents])
    args = ctx.actions.args()
    args.add("--output", jar)
    args.add_all(inputs, before_each = "--input")
    overlay_inputs = []
    for dep, version in ctx.attr.overlays.items():
        for member in dep.files.to_list():
            args.add("--overlay")
            args.add(version)
            args.add(member)
            overlay_inputs.append(member)
    for key, value in ctx.attr.manifest.items():
        args.add("--manifest-entry", key + "=" + value)
    if ctx.attr.snippets_pattern:
        args.add("--snippets-pattern", ctx.attr.snippets_pattern)
        for lib in ctx.attr.archived_libraries:
            args.add_all(lib.files, before_each = "--archived-library")
    if ctx.attr.graal_services:
        args.add("--graal-test-services" if ctx.attr.graal_test_services else "--graal-services")
    if ctx.attr.truffle_services:
        args.add("--truffle-services")
    if ctx.attr.jmh_metadata:
        args.add("--jmh-metadata")
    if ctx.attr.jni_services:
        args.add("--jni-services")
    uses_jvmci = ctx.attr.requires_jvmci or any([
        dep[MxJarInfo].uses_jvmci
        for dep in ctx.attr.deps
        if MxJarInfo in dep
    ])
    tools = []
    if ctx.attr.module_name:
        if uses_jvmci:
            toolchain = ctx.attr._labs_jvmci[java_common.JavaToolchainInfo]
            args.add("--system-modules")
        runtime = toolchain.java_runtime
        tools.append(runtime.files)
        args.add("--java", runtime.java_executable_exec_path)
        args.add("--release", ctx.attr.release)
        args.add("--module", ctx.attr.module_name)
        args.add_all(ctx.attr.directives, before_each = "--directive")
        module_path = depset(transitive = [dep.transitive_runtime_jars for dep in dependencies])
        args.add_all(module_path, before_each = "--module-jar")
        tools.append(module_path)
    ctx.actions.run(
        executable = ctx.executable._packer,
        arguments = [args],
        inputs = depset(overlay_inputs, transitive = [inputs] + tools),
        outputs = [jar],
        mnemonic = "MxJar",
        toolchain = _TOOLCHAIN,
    )
    abi = java_common.run_ijar(ctx.actions, jar, target_label = ctx.label, java_toolchain = toolchain)
    return [
        DefaultInfo(files = depset([jar])),
        MxJarInfo(
            uses_jvmci = uses_jvmci,
            required_exports = depset(
                ["--add-exports=" + package + "=" + ctx.attr.module_name for package in sorted({p: True for p in ctx.attr.add_exports + ctx.attr.module_exports})] if ctx.attr.module_name else [],
                transitive = [dep[MxJarInfo].required_exports for dep in ctx.attr.deps if MxJarInfo in dep],
            ),
        ),
        JavaInfo(output_jar = jar, compile_jar = abi, exports = dependencies, add_exports = ctx.attr.add_exports),
        coverage_common.instrumented_files_info(ctx, dependency_attributes = ["contents", "deps"]),
    ]

_jar_dist = rule(
    implementation = _jar_impl,
    attrs = {
        "requires_jvmci": attr.bool(),
        "_labs_jvmci": attr.label(default = Label("//tools:labs_jvmci"), cfg = "exec"),
        "overlays": attr.label_keyed_string_dict(providers = [JavaInfo]),
        "contents": attr.label_list(providers = [JavaInfo], mandatory = True),
        "deps": attr.label_list(providers = [JavaInfo]),
        "manifest": attr.string_dict(),
        "module_name": attr.string(),
        "release": attr.int(),
        "directives": attr.string_list(),
        "jni_services": attr.bool(),
        "jmh_metadata": attr.bool(),
        "truffle_services": attr.bool(),
        "graal_services": attr.bool(),
        "graal_test_services": attr.bool(),
        "snippets_pattern": attr.string(),
        "archived_libraries": attr.label_list(providers = [JavaInfo]),
        "add_exports": attr.string_list(),
        "module_exports": attr.string_list(),
        "_packer": attr.label(default = Label("//mx:pack_jar"), executable = True, cfg = "exec"),
    },
    toolchains = [_TOOLCHAIN],
    provides = [JavaInfo],
)

def mx_jar_dist(name, directives = [], **kwargs):
    """Use the Labs system image for descriptors that read JVMCI."""
    jvmci = any([d.split(" ")[-1] in ["jdk.internal.vm.ci", "jdk.graal.compiler"] for d in directives if d.startswith("requires ")])
    _jar_dist(
        name = name,
        directives = directives,
        requires_jvmci = jvmci,
        **kwargs
    )
