"""Package the platform-specific VisualVM payload declared by mx."""

load("@bazel_tools//tools/build_defs/repo:http.bzl", "http_file")

def _visualvm_inputs_impl(ctx):
    pins = json.decode(ctx.read(Label("//graal:visualvm.json")))
    for name, pin in pins.items():
        http_file(
            name = "visualvm_" + name,
            urls = pin["urls"],
            integrity = pin["integrity"],
            downloaded_file_path = "visualvm.tar.gz",
        )

visualvm_inputs = module_extension(implementation = _visualvm_inputs_impl)

def _visualvm_impl(ctx):
    output = ctx.actions.declare_file(ctx.label.name.lower().replace("_", "-") + ".tar")
    ctx.actions.run(
        executable = ctx.executable._pack,
        inputs = [ctx.file.common, ctx.file.platform, ctx.file.license_file],
        arguments = [output.path, ctx.file.common.path, ctx.file.platform.path, ctx.file.license_file.path],
        outputs = [output],
        mnemonic = "MxVisualVM",
    )
    return [DefaultInfo(files = depset([output]))]

_package = rule(
    implementation = _visualvm_impl,
    attrs = {
        "common": attr.label(allow_single_file = True, mandatory = True),
        "platform": attr.label(allow_single_file = True, mandatory = True),
        "license_file": attr.label(allow_single_file = True, mandatory = True),
        "_pack": attr.label(default = Label("//graal:pack_visualvm"), executable = True, cfg = "exec"),
    },
)

def visualvm_support(name):
    variants = {
        Label("//tools:exec_linux_amd64"): "@visualvm_linux_amd64//file",
        Label("//tools:exec_linux_arm64"): "@visualvm_linux_arm64//file",
        Label("//tools:exec_macos_arm64"): "@visualvm_macos//file",
        Label("//graal:visualvm_macos_x86_64"): "@visualvm_macos//file",
        Label("//graal:visualvm_windows_x86_64"): "@visualvm_windows_amd64//file",
    }
    _package(
        name = name,
        common = "@visualvm_common//file",
        platform = select(variants, no_match_error = "VisualVM has no upstream archive for this platform"),
        license_file = "VISUALVM_LICENSE",
        target_compatible_with = select({key: [] for key in variants} | {"//conditions:default": ["@platforms//:incompatible"]}),
        visibility = ["//visibility:public"],
    )
