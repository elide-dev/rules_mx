"""Oracle Labs JDK for suites that require the JVMCI build required by the selected Graal compiler."""

load("@rules_java//toolchains:remote_java_repository.bzl", "remote_java_repository")

_PLATFORMS = {
    "darwin-aarch64": ("aarch64", "macos"),
    "linux-aarch64": ("aarch64", "linux"),
    "linux-amd64": ("x86_64", "linux"),
}

def _labsjdk_impl(ctx):
    # tools/generate-graal keeps this pin equal to labsjdk-ce-latest in the selected Graal common.json.
    pin = json.decode(ctx.read(Label("//mx:labsjdk.json")))
    version = pin["version"]
    jdk, jvmci = version.split("-jvmci-")
    release = "jvmci-" + jvmci
    home = "labsjdk-" + jdk.split("+")[0] + "-" + release
    for platform, digest in pin["platforms"].items():
        cpu, os = _PLATFORMS[platform]
        remote_java_repository(
            name = "labsjdk_" + platform.replace("-", "_"),
            version = "25",
            prefix = "labsjdk",
            sha256 = digest,
            strip_prefix = home + ("/Contents/Home" if os == "macos" else ""),
            urls = ["https://github.com/graalvm/labs-openjdk/releases/download/%s/labsjdk-%s-%s.tar.gz" % (release, version.replace("+", "%2B"), platform)],
            target_compatible_with = ["@platforms//cpu:" + cpu, "@platforms//os:" + os],
        )
    return ctx.extension_metadata(reproducible = True)

labsjdk = module_extension(implementation = _labsjdk_impl)
