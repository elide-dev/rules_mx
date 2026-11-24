"""Oracle Labs JDK for suites that require the JVMCI build pinned by mx."""

load("@rules_java//toolchains:remote_java_repository.bzl", "remote_java_repository")

def _labsjdk_impl(ctx):
    # Matches labsjdk-ce-latest in the pinned mx common.json.
    for platform, cpu, os, digest in [
        ("darwin-aarch64", "aarch64", "macos", "55b35c84bb6143a6b5fa24299c54448b6a4a198d3f96d16c74e0c391fc17a52f"),
        ("linux-aarch64", "aarch64", "linux", "cdf43e6b9f64a7f05bcebbf098bd04dfecf2e28d63bcadc812c6e2cd724a2324"),
        ("linux-amd64", "x86_64", "linux", "75ab833495f9ebb369d320554c668beb671b0ae91d50e6bdeb377474d5a7ea30"),
    ]:
        remote_java_repository(
            name = "labsjdk_" + platform.replace("-", "_"),
            version = "25",
            prefix = "labsjdk",
            sha256 = digest,
            strip_prefix = "labsjdk-ce-25.0.2-jvmci-25.1-b16" + ("/Contents/Home" if os == "macos" else ""),
            urls = ["https://github.com/graalvm/labs-openjdk/releases/download/jvmci-25.1-b16/labsjdk-ce-25.0.2%2B10-jvmci-25.1-b16-" + platform + ".tar.gz"],
            target_compatible_with = ["@platforms//cpu:" + cpu, "@platforms//os:" + os],
        )
    return ctx.extension_metadata(reproducible = True)

labsjdk = module_extension(implementation = _labsjdk_impl)
