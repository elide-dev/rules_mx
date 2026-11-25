"""Oracle Labs JDK for suites that require the JVMCI build required by the pinned Graal compiler."""

load("@rules_java//toolchains:remote_java_repository.bzl", "remote_java_repository")

def _labsjdk_impl(ctx):
    # Matches labsjdk-ce-latest in the pinned Graal common.json.
    for platform, cpu, os, digest in [
        ("darwin-aarch64", "aarch64", "macos", "08d73b48434c033be7fb52f92c8a05f7579af575d2cfd4a26255c9f9cea5083a"),
        ("linux-aarch64", "aarch64", "linux", "d82205166184bcd4687657942ea2919cec72328e4717b845b7a635efbacf04e6"),
        ("linux-amd64", "x86_64", "linux", "871a0de0c24c35cbc17b364f9c3e322c709a85edd52d7ad5143bbb302c41232f"),
    ]:
        remote_java_repository(
            name = "labsjdk_" + platform.replace("-", "_"),
            version = "25",
            prefix = "labsjdk",
            sha256 = digest,
            strip_prefix = "labsjdk-ce-25.0.4.1.1-jvmci-25.4-b23" + ("/Contents/Home" if os == "macos" else ""),
            urls = ["https://github.com/graalvm/labs-openjdk/releases/download/jvmci-25.4-b23/labsjdk-ce-25.0.4.1.1%2B1-jvmci-25.4-b23-" + platform + ".tar.gz"],
            target_compatible_with = ["@platforms//cpu:" + cpu, "@platforms//os:" + os],
        )
    return ctx.extension_metadata(reproducible = True)

labsjdk = module_extension(implementation = _labsjdk_impl)
