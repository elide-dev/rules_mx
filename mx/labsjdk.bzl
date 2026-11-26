"""Oracle Labs JDK for suites that require the JVMCI build required by the selected Graal compiler."""

load("@rules_java//toolchains:remote_java_repository.bzl", "remote_java_repository")

_PLATFORMS = {
    "darwin-aarch64": ("aarch64", "macos"),
    "linux-aarch64": ("aarch64", "linux"),
    "linux-amd64": ("x86_64", "linux"),
}

def _register(pin, prefix, repository):
    version = pin["version"]
    jdk, jvmci = version.split("-jvmci-")
    release = "jvmci-" + jvmci
    home = "labsjdk-" + jdk.split("+")[0] + "-" + release
    for platform, digest in pin["platforms"].items():
        cpu, os = _PLATFORMS[platform]
        remote_java_repository(
            name = prefix + "_" + platform.replace("-", "_"),
            version = jdk.removeprefix("ce-").split(".")[0],
            prefix = prefix,
            sha256 = digest,
            patches = [Label("//mx:jdk_release.patch")] if prefix == "labsjdk" else [],
            patch_args = ["-p1"],
            strip_prefix = home + ("/Contents/Home" if os == "macos" else ""),
            urls = ["https://github.com/graalvm/%s/releases/download/%s/labsjdk-%s-%s.tar.gz" % (repository, release, version.replace("+", "%2B"), platform)],
            target_compatible_with = ["@platforms//cpu:" + cpu, "@platforms//os:" + os],
        )

def _labsjdk_impl(ctx):
    _register(json.decode(ctx.read(Label("//mx:labsjdk.json"))), "labsjdk", "labs-openjdk")
    _register(json.decode(ctx.read(Label("//mx:labsjdk21.json"))), "espresso_jdk21", "labs-openjdk-21")
    return ctx.extension_metadata(reproducible = True)

labsjdk = module_extension(implementation = _labsjdk_impl)
