"""Build Truffle's JNI attach library and its target-platform internal resource."""

load("@rules_cc//cc:cc_binary.bzl", "cc_binary")
load("@rules_cc//cc:cc_library.bzl", "cc_library")
load("@rules_java//java/common:java_info.bzl", "JavaInfo")

def _jvmti_header_impl(ctx):
    runtime = ctx.toolchains["@bazel_tools//tools/jdk:runtime_toolchain_type"].java_runtime
    headers = [f for f in runtime.files.to_list() if f.path == runtime.java_home + "/include/jvmti.h"]
    if len(headers) != 1:
        fail("The target Java runtime must supply include/jvmti.h")
    header = ctx.actions.declare_file("include/jvmti.h")
    ctx.actions.symlink(output = header, target_file = headers[0])
    return [DefaultInfo(files = depset([header]))]

_jvmti_header = rule(
    implementation = _jvmti_header_impl,
    toolchains = ["@bazel_tools//tools/jdk:runtime_toolchain_type"],
)

def _resource_impl(ctx):
    jar = ctx.actions.declare_file(ctx.label.name + ".jar")
    library = ctx.file.library
    args = ctx.actions.args()
    args.add(jar)
    args.add(library)
    args.add(ctx.attr.destination + library.basename)
    args.add(ctx.attr.file_list)
    args.add(ctx.attr.hash_entry)
    ctx.actions.run(
        executable = ctx.executable._pack,
        arguments = [args],
        inputs = [library],
        outputs = [jar],
        mnemonic = "MxNativeResource",
    )
    return [
        DefaultInfo(files = depset([jar])),
        JavaInfo(output_jar = jar, compile_jar = None),
    ]

_resource = rule(
    implementation = _resource_impl,
    attrs = {
        "library": attr.label(allow_single_file = True, mandatory = True),
        "destination": attr.string(mandatory = True),
        "file_list": attr.string(mandatory = True),
        "hash_entry": attr.string(mandatory = True),
        "_pack": attr.label(default = Label("//mx:pack_native_resource"), executable = True, cfg = "exec"),
    },
)

def truffle_attach(name, deliverable, srcs, cflags, ldlibs, resource, platforms, destination, file_list, hash_entry):
    """Lower the shared library and layout from the pinned Truffle suite."""
    _jvmti_header(name = name + ".header")
    cc_library(
        name = name + ".jvmti",
        hdrs = [":" + name + ".header"],
        strip_include_prefix = "include",
        deps = ["@rules_java//toolchains:jni"],
    )
    libraries = {}
    os_labels = {"darwin": "macos", "linux": "linux", "windows": "windows"}
    names = {"darwin": "lib%s.dylib", "linux": "lib%s.so", "windows": "%s.dll"}
    for os, flags in cflags.items():
        constraint = "@platforms//os:" + os_labels[os]
        filename = names[os] % deliverable
        cc_binary(
            name = filename,
            srcs = srcs,
            copts = flags,
            linkopts = ldlibs[os],
            linkshared = True,
            linkstatic = True,
            target_compatible_with = [constraint],
            deps = [":" + name + ".jvmti"],
        )
        libraries[constraint] = ":" + filename
    native.alias(name = name, actual = select(libraries), visibility = ["//visibility:public"])
    truffle_resource(resource, ":" + name, platforms, destination, file_list, hash_entry)

def truffle_resource(resource, library, platforms, destination, file_list, hash_entry):
    """Package a target shared library at mx's platform-qualified internal resource path."""
    os_labels = {"darwin": "macos", "linux": "linux", "windows": "windows"}
    paths = {template: {} for template in [destination, file_list, hash_entry]}
    for platform in platforms:
        os, cpu = platform.split("-")
        config = resource + "." + platform
        native.config_setting(
            name = config,
            constraint_values = [
                "@platforms//os:" + os_labels[os],
                "@platforms//cpu:" + {"amd64": "x86_64", "aarch64": "aarch64"}[cpu],
            ],
        )
        for template, choices in paths.items():
            choices[":" + config] = template.replace("<os>", os).replace("<arch>", cpu)
    _resource(
        name = resource,
        library = library,
        destination = select(paths[destination]),
        file_list = select(paths[file_list]),
        hash_entry = select(paths[hash_entry]),
        visibility = ["//visibility:public"],
    )
