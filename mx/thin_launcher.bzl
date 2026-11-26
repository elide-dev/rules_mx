"""Compile the upstream GraalVM JNI launcher with its mx-resolved configuration."""

load("@rules_cc//cc:cc_binary.bzl", "cc_binary")

def mx_thin_launcher(name, srcs, platform_copts, **kwargs):
    linux = "@platforms//os:linux"
    macos = "@platforms//os:macos"
    cc_binary(
        name = name,
        srcs = srcs,
        copts = [
            "-std=c++17",
            "-O3",
            "-pthread",
            "-Werror=undef",
            "-DCP_SEP=:",
            "-DDIR_SEP=/",
        ] + select({
            # The launcher uses pthread directly. libc++ need not include pthread.h.
            linux: platform_copts["linux"] + ["-stdlib=libc++", "-include", "pthread.h"],
            macos: platform_copts["macos"] + ["-ObjC++"],
            "//conditions:default": [],
        }),
        features = ["no_copts_tokenization"],
        linkopts = select({
            linux: ["-pthread", "-ldl", "-stdlib=libc++", "-static-libstdc++", "-l:libc++abi.a"],
            macos: ["-pthread", "-ldl", "-framework", "Foundation"],
            "//conditions:default": [],
        }),
        target_compatible_with = select({linux: [], macos: [], "//conditions:default": ["@platforms//:incompatible"]}),
        deps = ["@rules_java//toolchains:jni"],
        **kwargs
    )
