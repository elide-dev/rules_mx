"""Pinned LLVM archives shared with Komodo (elide-dev/toolchain)."""

load("@bazel_tools//tools/build_defs/repo:http.bzl", "http_archive")

ELIDE_TOOLCHAIN = "2026.11.7"

ELIDE_TOOLCHAIN_BUNDLES = {
    "darwin_arm64": ("darwin-arm64", "69d312ff9d0bf7158942e9d7220278930c4106d5b246482485e2f7da1a6db4c7"),
    "linux_amd64": ("linux-amd64", "697cb494450d47630629e5ac905bca8400611030361305e32b86eb818ae407a5"),
    "linux_arm64": ("linux-arm64", "ed353fbc779d2179de2c0631c6e1de56fe9ebda3a981460d01d34239da4058a8"),
}

def _llvm(ctx):
    for key, (platform, sha) in ELIDE_TOOLCHAIN_BUNDLES.items():
        asset = "elide-toolchain-{}-{}.tar.xz".format(ELIDE_TOOLCHAIN, platform)
        commands = ["chmod +x cc"]
        if key.startswith("linux"):
            commands.append('probe=$(mktemp -d ./casecheck.XXXXXX); touch "$probe/lower"; if test -e "$probe/LOWER"; then echo "Linux sysroots require a case-sensitive Bazel output base" >&2; exit 1; fi; rm -r "$probe"')
        http_archive(
            name = "elide_toolchain_" + key,
            urls = [
                "https://static.elideusercontent.com/toolchain/{}/{}".format(ELIDE_TOOLCHAIN, asset),
                "https://github.com/elide-dev/toolchain/releases/download/v{}/{}".format(ELIDE_TOOLCHAIN, asset),
            ],
            sha256 = sha,
            strip_prefix = "elide-toolchain",
            build_file = Label("//tools/cc:toolchain.BUILD"),
            patches = [Label("//tools/cc:cc.patch")],
            patch_strip = 1,
            patch_cmds = commands,
        )
    return ctx.extension_metadata(reproducible = True)

llvm = module_extension(implementation = _llvm)
