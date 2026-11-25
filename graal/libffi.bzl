"""Build the mx-pinned libffi release with the BCR native cc_library overlay."""

_OVERLAY = "https://bcr.bazel.build/modules/libffi/3.4.7.bcr.5/overlay/"
_OVERLAY_DIGESTS = {
    "BUILD.bazel": "sha256-LYXTn1VY1Lqx2i4QMSXrixUf/UzwQmDEJuDM/LP4wII=",
    "fficonfig.h.in": "sha256-DHca/b6enWso+TAEEnzvKZWUyxDoIhQ5qm9RH/7Eo30=",
    "libffi.bzl": "sha256-Kzczj+HATmjjWvg6YSNRVshzZH6RrRUZA/l2DzEQuxs=",
    "msvc_asm.bzl": "sha256-JwOtmCOBJmLaz/lupnvHNjJSGNbjIR0GtGGW3gZALaA=",
}

def _libffi_impl(ctx):
    pin = json.decode(ctx.read(ctx.attr.lock))
    ctx.download_and_extract(
        url = pin["urls"],
        integrity = pin["integrity"],
        stripPrefix = "libffi-" + pin["version"],
    )
    for path, integrity in _OVERLAY_DIGESTS.items():
        ctx.download(url = _OVERLAY + path, output = path, integrity = integrity)

    # This repository follows the mx source version independently of the BCR module.
    build = ctx.read("BUILD.bazel").replace("module_version()", repr(pin["version"]))

    # The pinned GNU/Linux sysroot exposes glibc 2.17 headers. Use libffi's
    # portable fallback for the optional memfd_create API (glibc 2.27+).
    build = build.replace('"HAVE_MEMFD_CREATE": "1"', '"HAVE_MEMFD_CREATE": None')
    build += """
alias(name = "ffi_distribution_header", actual = ":ffi_h", visibility = ["//visibility:public"])
alias(name = "ffitarget_distribution_header", actual = ":ffitarget_h", visibility = ["//visibility:public"])
"""
    ctx.file("BUILD.bazel", build)

graal_libffi = repository_rule(
    implementation = _libffi_impl,
    attrs = {"lock": attr.label(mandatory = True)},
)
