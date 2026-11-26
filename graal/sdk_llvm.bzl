"""Redistribute the checksum-pinned LLVM toolchain used by the mx SDK suite."""

load("@bazel_tools//tools/build_defs/repo:http.bzl", "http_file")
load("//graal:layout_info.bzl", "MxArchiveLayoutInfo")

def _register_archives(ctx, lock, prefix, filename):
    pins = json.decode(ctx.read(Label(lock)))
    for name, pin in pins.items():
        http_file(
            name = prefix + name,
            urls = pin["urls"],
            integrity = pin["integrity"],
            downloaded_file_path = filename,
        )

def _inputs_impl(ctx):
    _register_archives(ctx, "//graal:sdk_llvm.json", "graal_llvm_", "llvm.tar.gz")
    _register_archives(ctx, "//graal:sdk_musl.json", "graal_musl_", "toolchain.tar.gz")

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
    return [DefaultInfo(files = depset([output])), MxArchiveLayoutInfo(archive = output, entries = {}, tar_trees = [(output, "")])]

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

def _descriptor_impl(ctx):
    configuration = ctx.actions.declare_file(ctx.label.name.lower() + "." + ctx.attr.kind)
    ctx.actions.write(configuration, ctx.attr.content)
    root = ctx.actions.declare_directory(ctx.label.name.lower())
    ctx.actions.run(
        executable = ctx.executable._assemble,
        inputs = [ctx.file.payload, configuration],
        outputs = [root],
        arguments = [root.path, ctx.file.payload.path, configuration.path, "toolchain." + ctx.attr.kind],
        mnemonic = "MxSDKToolchain",
    )
    return [DefaultInfo(files = depset([root]))]

_descriptor = rule(
    implementation = _descriptor_impl,
    attrs = {
        "payload": attr.label(allow_single_file = True, mandatory = True),
        "content": attr.string(mandatory = True),
        "kind": attr.string(values = ["ninja", "cmake"], mandatory = True),
        "_assemble": attr.label(default = Label("//graal:assemble_sdk_toolchain"), executable = True, cfg = "exec"),
    },
)

def sdk_toolchain_descriptors(descriptors):
    for name, definition in descriptors.items():
        kind = definition["kind"]
        variants = {":llvm_" + target: content for target, content in definition["platforms"].items()}
        payload = ":LLVM_TOOLCHAIN" if name == "LLVM_NINJA_TOOLCHAIN" else select({
            ":llvm_linux_amd64": "@graal_musl_amd64//file",
            ":llvm_linux_aarch64": "@graal_musl_aarch64//file",
        })
        _descriptor(
            name = name,
            kind = kind,
            content = select(variants),
            payload = payload,
            tags = ["manual"],
            target_compatible_with = select({setting: [] for setting in variants} | {"//conditions:default": ["@platforms//:incompatible"]}),
            visibility = ["//visibility:public"],
        )

def _llvm_link_impl(ctx):
    tool = ctx.actions.declare_file(ctx.label.name + "/bin/llvm-link")
    libraries = ctx.actions.declare_directory(ctx.label.name + "/lib")
    ctx.actions.run(
        executable = ctx.executable._extract,
        inputs = [ctx.file.archive],
        outputs = [tool, libraries],
        arguments = [ctx.file.archive.path, tool.path, libraries.path],
        mnemonic = "ExtractSDKLLVMLink",
    )
    return [DefaultInfo(executable = tool, files = depset([tool, libraries]), runfiles = ctx.runfiles(files = [libraries]))]

sdk_llvm_link = rule(
    implementation = _llvm_link_impl,
    executable = True,
    attrs = {
        "archive": attr.label(allow_single_file = True, mandatory = True),
        "_extract": attr.label(default = Label("//graal:extract_llvm_link"), cfg = "exec", executable = True),
    },
)

def _fixture_sdk_impl(ctx):
    root = ctx.actions.declare_directory(ctx.label.name + ".files")
    ctx.actions.run(
        executable = ctx.executable._extract,
        inputs = [ctx.file.archive],
        outputs = [root],
        arguments = [ctx.file.archive.path, root.path],
        mnemonic = "ExtractSulongSDK",
    )
    return [DefaultInfo(files = depset([root]))]

sulong_fixture_sdk = rule(
    implementation = _fixture_sdk_impl,
    attrs = {
        "archive": attr.label(allow_single_file = True, mandatory = True),
        "_extract": attr.label(default = Label("//graal:extract_fixture_sdk"), cfg = "exec", executable = True),
    },
)
