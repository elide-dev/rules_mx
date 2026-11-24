"""Pinned LLVM archives shared with Komodo (elide-dev/toolchain)."""

load("@bazel_tools//tools/build_defs/repo:http.bzl", "http_archive")

ELIDE_TOOLCHAIN = "2026.11.7"

ELIDE_TOOLCHAIN_BUNDLES = {
    "darwin_arm64": ("darwin-arm64", "arm64-apple-darwin", "69d312ff9d0bf7158942e9d7220278930c4106d5b246482485e2f7da1a6db4c7"),
    "linux_amd64": ("linux-amd64", "x86_64-unknown-linux-gnu", "697cb494450d47630629e5ac905bca8400611030361305e32b86eb818ae407a5"),
    "linux_arm64": ("linux-arm64", "aarch64-unknown-linux-gnu", "ed353fbc779d2179de2c0631c6e1de56fe9ebda3a981460d01d34239da4058a8"),
}

def _installed_apple_sdk(ctx):
    # Komodo's LLVM 23 linker understands SDK stubs through macOS 26.
    explicit = ctx.getenv("SDKROOT")
    if explicit:
        return ctx.path(explicit)
    ctx.getenv("DEVELOPER_DIR")
    ctx.watch("/var/db/xcode_select_link")
    selected = ctx.execute(["/usr/bin/xcrun", "--sdk", "macosx", "--show-sdk-path"])
    candidates = [ctx.path(selected.stdout.strip())] if selected.return_code == 0 else []
    clt = ctx.path("/Library/Developer/CommandLineTools/SDKs")
    if clt.exists:
        candidates.append(clt.get_child("MacOSX.sdk"))
        candidates.extend(clt.readdir())
    for sdk in candidates:
        settings = sdk.get_child("SDKSettings.json")
        if settings.exists and int(json.decode(ctx.read(settings))["Version"].split(".")[0]) <= 26:
            return sdk
    fail("Select a compatible SDK with SDKROOT or configure llvm.darwin_sdk in the root module")

def _sdk_files(root):
    # Apple frameworks use directory aliases. Some (e.g. Ruby's Headers/ruby/ruby)
    # point back to an ancestor. Preserve each usable alias and stop at cycles.
    files = []
    dirs = [(root, "apple-sdk", [root.realpath])]
    for _ in range(256):
        children = []
        for directory, prefix, ancestors in dirs:
            for entry in directory.readdir():
                name = prefix + "/" + entry.basename
                if name == "apple-sdk/usr/share":
                    continue  # Manual pages are not compiler inputs.
                if entry.is_dir:
                    real = entry.realpath
                    if real not in ancestors:
                        children.append((entry, name, ancestors + [real]))
                elif entry.exists:
                    files.append(name)
        if not children:
            return sorted(files)
        dirs = children
    fail("Apple SDK directory nesting exceeds 256 levels")

def _cc_bundle_impl(ctx):
    darwin = ctx.attr.triple == "arm64-apple-darwin"
    if not darwin:
        ctx.file("casecheck", "")
        if ctx.path("CASECHECK").exists:
            fail("Linux sysroots require a case-sensitive Bazel output base")
        ctx.delete("casecheck")
    archive = ctx.path(ctx.attr.archive).dirname
    for directory in ["bin", "include", "lib", "sysroot"]:
        ctx.symlink(archive.get_child(directory), directory)
    if ctx.attr.sdk_urls:
        ctx.download_and_extract(
            ctx.attr.sdk_urls,
            output = "apple-sdk",
            sha256 = ctx.attr.sdk_sha256,
            strip_prefix = ctx.attr.sdk_strip_prefix,
        )
    elif darwin and ctx.os.name == "mac os x":
        ctx.symlink(_installed_apple_sdk(ctx), "apple-sdk")
    elif darwin:
        fail("Configure llvm.darwin_sdk in the root module to build for Darwin from this host")
    sdk = ctx.path("apple-sdk")
    files = []
    if sdk.exists:
        if not sdk.get_child("SDKSettings.json").exists or not sdk.get_child("usr/lib/libSystem.tbd").exists:
            fail("The Apple SDK must contain SDKSettings.json and usr/lib/libSystem.tbd. Check the SDK path or strip_prefix.")
        files = _sdk_files(sdk.realpath)
    ctx.file("sdk_files.bzl", "SDK_FILES = " + repr(files) + "\n")

    # Use the bundle's driver options without clang expanding <CFGDIR> into a
    # machine-specific absolute path. These pinned configs contain plain tokens.
    cfg = ctx.read("bin/" + ctx.attr.triple + ".cfg")
    args = [arg.replace("<CFGDIR>", "${here}/bin") for line in cfg.split("\n") for arg in line.split(" ") if arg]
    if darwin:
        args.extend(["-isysroot", "${here}/apple-sdk"])
    ctx.template("cc", ctx.attr._wrapper, {"@DRIVER_FLAGS@": " ".join(['"' + arg + '"' for arg in args])})
    ctx.symlink(ctx.attr._build, "BUILD.bazel")
    return ctx.repo_metadata(reproducible = not darwin or bool(ctx.attr.sdk_urls))

_cc_bundle = repository_rule(
    implementation = _cc_bundle_impl,
    attrs = {
        "archive": attr.label(mandatory = True),
        "triple": attr.string(mandatory = True),
        "sdk_urls": attr.string_list(),
        "sdk_sha256": attr.string(),
        "sdk_strip_prefix": attr.string(),
        "_build": attr.label(default = "//tools/cc:toolchain.BUILD"),
        "_wrapper": attr.label(default = "//tools/cc:cc.sh.tpl"),
    },
)

def _llvm(ctx):
    sdk = None
    for mod in ctx.modules:
        for tag in mod.tags.darwin_sdk:
            if not mod.is_root or sdk != None:
                fail("The root module can specify one darwin_sdk")
            if not tag.urls or len(tag.sha256) != 64:
                fail("darwin_sdk requires archive URLs and a SHA-256 digest")
            sdk = tag
    for key, (platform, triple, sha) in ELIDE_TOOLCHAIN_BUNDLES.items():
        asset = "elide-toolchain-{}-{}.tar.xz".format(ELIDE_TOOLCHAIN, platform)
        urls = [
            "https://static.elideusercontent.com/toolchain/{}/{}".format(ELIDE_TOOLCHAIN, asset),
            "https://github.com/elide-dev/toolchain/releases/download/v{}/{}".format(ELIDE_TOOLCHAIN, asset),
        ]
        archive = "elide_toolchain_" + key + "_archive"
        http_archive(
            name = archive,
            urls = urls,
            sha256 = sha,
            strip_prefix = "elide-toolchain",
            build_file_content = 'exports_files(["BUILD.bazel"])',
        )
        apple_sdk = sdk if key == "darwin_arm64" else None
        _cc_bundle(
            name = "elide_toolchain_" + key,
            archive = "@" + archive + "//:BUILD.bazel",
            triple = triple,
            sdk_urls = apple_sdk.urls if apple_sdk else [],
            sdk_sha256 = apple_sdk.sha256 if apple_sdk else "",
            sdk_strip_prefix = apple_sdk.strip_prefix if apple_sdk else "",
        )
    return ctx.extension_metadata(reproducible = True)

llvm = module_extension(
    implementation = _llvm,
    tag_classes = {
        "darwin_sdk": tag_class(attrs = {
            "urls": attr.string_list(mandatory = True),
            "sha256": attr.string(mandatory = True),
            "strip_prefix": attr.string(),
        }),
    },
)
