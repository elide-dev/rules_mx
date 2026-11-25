"""Redistribute the checksum-pinned LLVM toolchain used by the mx SDK suite."""

load("@bazel_tools//tools/build_defs/repo:http.bzl", "http_file")

def _inputs_impl(ctx):
    pins = json.decode(ctx.read(Label("//graal:sdk_llvm.json")))
    for name, pin in pins.items():
        http_file(
            name = "graal_llvm_" + name,
            urls = pin["urls"],
            integrity = pin["integrity"],
            downloaded_file_path = "llvm.tar.gz",
        )

sdk_llvm_inputs = module_extension(implementation = _inputs_impl)

def _strip_impl(ctx):
    output = ctx.actions.declare_file("llvm-toolchain.tar.gz")
    args = ctx.actions.args()
    args.add(output)
    args.add(ctx.file.llvm)
    args.add(ctx.file.compiler_rt)
    if ctx.attr.dereference:
        args.add("--dereference")
    args.add_all(ctx.attr.exclude, before_each = "--exclude")
    for resource in ctx.files.resources:
        args.add("--resource")
        args.add(resource.basename if resource == ctx.file.license_file else "patches/" + resource.basename)
        args.add(resource.path)
    ctx.actions.run(
        executable = ctx.executable._pack,
        inputs = [ctx.file.llvm, ctx.file.compiler_rt] + ctx.files.resources,
        outputs = [output],
        arguments = [args],
        mnemonic = "MxLLVMArchive",
    )
    return [DefaultInfo(files = depset([output]))]

_strip = rule(
    implementation = _strip_impl,
    attrs = {
        "llvm": attr.label(allow_single_file = True, mandatory = True),
        "compiler_rt": attr.label(allow_single_file = True, mandatory = True),
        "exclude": attr.string_list(),
        "dereference": attr.bool(),
        "license_file": attr.label(allow_single_file = True, mandatory = True),
        "resources": attr.label_list(allow_files = True),
        "_pack": attr.label(default = Label("//graal:pack_sdk_llvm"), executable = True, cfg = "exec"),
    },
)

def sdk_llvm_distributions(exclude, license_file, patches):
    variants = {}
    for platform, os, cpu in [
        ("linux_amd64", "linux", "x86_64"),
        ("linux_aarch64", "linux", "aarch64"),
        ("linux_riscv64", "linux", "riscv64"),
        ("darwin_aarch64", "macos", "aarch64"),
        ("windows_amd64", "windows", "x86_64"),
    ]:
        setting = "llvm_" + platform
        native.config_setting(name = setting, constraint_values = ["@platforms//os:" + os, "@platforms//cpu:" + cpu])
        variants[":" + setting] = "@graal_llvm_" + platform + "//file"

    compatibility = select({setting: [] for setting in variants} | {"//conditions:default": ["@platforms//:incompatible"]})
    native.alias(
        name = "LLVM_TOOLCHAIN_FULL",
        actual = select(variants),
        tags = ["manual"],
        target_compatible_with = compatibility,
        visibility = ["//visibility:public"],
    )
    _strip(
        name = "LLVM_TOOLCHAIN",
        llvm = ":LLVM_TOOLCHAIN_FULL",
        compiler_rt = "@graal_llvm_compiler_rt//file",
        dereference = select({":llvm_windows_amd64": True, "//conditions:default": False}),
        exclude = exclude,
        license_file = license_file,
        resources = [license_file] + native.glob([patches], allow_empty = False),
        tags = ["manual"],
        target_compatible_with = compatibility,
        visibility = ["//visibility:public"],
    )
