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
    ctx.file("BUILD.bazel", ctx.read("BUILD.bazel").replace("module_version()", repr(pin["version"])))

graal_libffi = repository_rule(
    implementation = _libffi_impl,
    attrs = {"lock": attr.label(mandatory = True)},
)
