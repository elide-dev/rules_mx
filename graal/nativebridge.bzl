"""Native Bridge launcher and mx's target-platform resource layout."""

load("@rules_cc//cc:cc_binary.bzl", "cc_binary")

def _resource_impl(ctx):
    binary = ctx.attr.binary[DefaultInfo].files_to_run.executable
    out = ctx.actions.declare_file(ctx.label.name + "/" + ctx.attr.directory + "/" + binary.basename)
    ctx.actions.symlink(output = out, target_file = binary, is_executable = True)
    return [DefaultInfo(files = depset([out]), runfiles = ctx.runfiles(files = [out]))]

_resource = rule(
    implementation = _resource_impl,
    attrs = {
        "binary": attr.label(executable = True, cfg = "target", mandatory = True),
        "directory": attr.string(mandatory = True),
    },
)

def nativebridge_launcher(name, deliverable, srcs, cflags, ldlibs, resource, platforms):
    """Use mx's declared compiler options, deliverable and layout platforms."""
    os_labels = {"darwin": "macos", "linux": "linux", "windows": "windows"}
    cpu_labels = {"amd64": "x86_64", "aarch64": "aarch64"}
    cc_binary(
        name = deliverable,
        srcs = srcs,
        copts = select({"@platforms//os:" + os_labels[os]: opts for os, opts in cflags.items()}),
        linkopts = select({"@platforms//os:" + os_labels[os]: ldlibs.get(os, []) for os in cflags}),
        deps = ["@rules_java//toolchains:jni"],
        visibility = ["//visibility:public"],
    )
    native.alias(name = name, actual = ":" + deliverable, visibility = ["//visibility:public"])
    directories = {}
    for platform in platforms:
        os, cpu = platform.split("-")
        cfg = resource + "." + platform
        native.config_setting(
            name = cfg,
            constraint_values = ["@platforms//os:" + os_labels[os], "@platforms//cpu:" + cpu_labels[cpu]],
        )
        directories[":" + cfg] = os + "/" + cpu
    _resource(name = resource, binary = ":" + deliverable, directory = select(directories), visibility = ["//visibility:public"])
