"""Bind mx discovery to the selected target-platform JDK."""

_RUNTIME = "@bazel_tools//tools/jdk:runtime_toolchain_type"

def _launcher(ctx):
    runtime = ctx.toolchains[_RUNTIME].java_runtime
    matches = [f for f in runtime.files.to_list() if f.path == runtime.java_executable_exec_path]
    if len(matches) != 1:
        fail("mx requires a file-backed Java runtime. Select --java_runtime_version=remotejdk_21.")
    short_path = matches[0].short_path
    java_key = short_path[3:] if short_path.startswith("../") else ctx.workspace_name + "/" + short_path
    ctx.actions.expand_template(
        template = ctx.file._template,
        output = ctx.outputs.out,
        substitutions = {"{JAVA_KEY}": java_key},
    )
    return [DefaultInfo(files = depset([ctx.outputs.out]), runfiles = ctx.runfiles(transitive_files = runtime.files))]

mx_launcher = rule(
    implementation = _launcher,
    attrs = {
        "out": attr.output(mandatory = True),
        "_template": attr.label(default = Label("//mx:launcher.py.tpl"), allow_single_file = True),
    },
    toolchains = [_RUNTIME],
)
