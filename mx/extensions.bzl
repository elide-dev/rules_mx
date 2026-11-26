"""Integrity-pinned mx sources and CLI."""

load("@bazel_tools//tools/build_defs/repo:http.bzl", "http_archive")

def _sources(_ctx):
    revision = "14d497f12cba5b0f33445fa3a894d83dd7e665f2"
    http_archive(
        name = "graal_mx",
        urls = ["https://codeload.github.com/graalvm/mx/tar.gz/" + revision],
        type = "tar.gz",
        sha256 = "d4f16b306b14a07e672799f3fae06a4d0dca156574a1134dc82866ed8d7e1581",
        strip_prefix = "mx-" + revision,
        build_file = Label("//mx:upstream.BUILD"),
        files = {"mx.mx/mx_bazel_export.py": Label("//mx:export.py")},
        patches = [Label("//mx:register.patch"), Label("//mx:module_descriptor.patch"), Label("//mx:symbolic_classpath.patch"), Label("//mx:declaration_name.patch")],
        patch_args = ["-p1"],
    )

mx_sources = module_extension(implementation = _sources)
