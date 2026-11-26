"""Expose suite-pinned source archives to Gazelle and individual compile actions."""

load("@bazel_tools//tools/build_defs/repo:http.bzl", "http_archive")

def _packed_sources(ctx):
    for name, pin in json.decode(ctx.read(Label("//graal:packed_sources.json"))).items():
        http_archive(
            name = name,
            urls = pin["urls"],
            integrity = pin["integrity"],
            strip_prefix = pin.get("subtree", ""),
            add_prefix = "src" if name == "graal_sdk_llvm_org_src" else pin.get("subtree", ""),
            build_file_content = ("""
package(default_visibility = ["//visibility:public"])
filegroup(name = "runtimes_sources", srcs = ["BUILD.bazel"] + glob([
    "src/runtimes/**", "src/cmake/**", "src/libcxx/**", "src/libcxxabi/**", "src/libunwind/**",
    "src/libc/shared/**", "src/libc/src/**", "src/libc/hdr/**", "src/libc/include/**", "src/libc/config/**",
    "src/llvm/cmake/**", "src/llvm/include/**", "src/llvm/utils/llvm-lit/**", "src/llvm/utils/lit/**",
], exclude = ["src/libcxx/test/**", "src/libcxxabi/test/**", "src/libcxx/benchmarks/**"]) + glob([
    "src/libcxx/test/configs/**", "src/libcxxabi/test/configs/**",
]))
""" if name == "graal_sdk_llvm_org_src" else """
package(default_visibility = ["//visibility:public"])
exports_files(["BUILD.bazel"] + glob(["**/*.c", "**/*.cpp", "**/*.cc", "**/*.C", "**/*.ll"], allow_empty = True))
filegroup(name = "headers", srcs = glob(["**/*.h", "**/*.hpp", "**/*.inc", "**/*.def"], allow_empty = True))
"""),
        )
    return ctx.extension_metadata(reproducible = True)

packed_sources = module_extension(implementation = _packed_sources)
