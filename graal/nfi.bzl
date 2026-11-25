"""Shared libraries, javac JNI headers and runfiles for Truffle NFI."""

load("@rules_cc//cc:cc_binary.bzl", "cc_binary")
load("@rules_cc//cc:cc_library.bzl", "cc_library")
load("@rules_java//java/common:java_common.bzl", "java_common")
load(":attach.bzl", "truffle_resource")

def _headers_impl(ctx):
    jar = ctx.file.jar
    outputs = [ctx.actions.declare_file("nfi-jni/" + h) for h in ctx.attr.headers]
    args = ctx.actions.args()
    args.add_all(["x", jar, "-d", outputs[0].dirname])
    args.add_all(ctx.attr.headers)
    ctx.actions.run(
        executable = ctx.executable._zipper,
        inputs = [jar],
        outputs = outputs,
        arguments = [args],
        mnemonic = "MxJniHeaders",
    )
    return [DefaultInfo(files = depset(outputs))]

_headers = rule(
    implementation = _headers_impl,
    attrs = {
        "jar": attr.label(allow_single_file = True, mandatory = True),
        "headers": attr.string_list(mandatory = True),
        "_zipper": attr.label(default = "@bazel_tools//tools/zip:zipper", cfg = "exec", executable = True),
    },
)

def _directory_impl(ctx):
    directory = ctx.actions.declare_directory(ctx.label.name)
    args = ctx.actions.args()
    args.add(directory.path)
    args.add_all(ctx.files.libs)
    ctx.actions.run(
        executable = ctx.executable._copy,
        arguments = [args],
        inputs = ctx.files.libs,
        outputs = [directory],
        mnemonic = "MxNativeTestLayout",
    )

    # mx includes this native distribution in Java dependency lists. Its classpath is empty.
    return [
        DefaultInfo(files = depset([directory]), runfiles = ctx.runfiles(files = [directory])),
        java_common.merge([]),
    ]

_directory = rule(
    implementation = _directory_impl,
    attrs = {
        "libs": attr.label_list(allow_files = True),
        "_copy": attr.label(default = Label("//graal:native_test_layout"), cfg = "exec", executable = True),
    },
)

def nfi_libraries(name, projects, benchmark, headers, platforms, destination, file_list, hash_entry):
    """Compile the NFI backend and test libraries using the target C/C++ toolchain."""
    _headers(name = "nfi_jni_files", jar = ":com.oracle.truffle.nfi.backend.libffi.jni_headers", headers = headers)
    cc_library(name = "nfi_jni", hdrs = [":nfi_jni_files"], strip_include_prefix = "nfi-jni", deps = ["@rules_java//toolchains:jni"])
    cc_library(name = "nfi_api_headers", hdrs = ["src/com.oracle.truffle.nfi.native/include/trufflenfi.h"], strip_include_prefix = "src/com.oracle.truffle.nfi.native/include")
    native.alias(name = "libffi", actual = "@graal_libffi//:libffi", visibility = ["//visibility:public"])
    for project in projects + [benchmark]:
        libraries = {}
        for os, ext in [("linux", ".so"), ("darwin", ".dylib")]:
            constraint = "@platforms//os:" + ("macos" if os == "darwin" else os)
            filename = "lib" + project["deliverable"] + ext
            options = project["os_arch"].get(os, project["os_arch"]["<others>"])["<others>"]
            cc_binary(
                name = filename,
                srcs = project["sources"],
                copts = options["cflags"],
                linkopts = options.get("ldflags", []) + options.get("ldlibs", []),
                linkshared = True,
                linkstatic = True,
                testonly = project == benchmark,
                target_compatible_with = [constraint],
                deps = [":nfi_api_headers"] + ([":nfi_jni", ":libffi"] if project["name"] == name else []),
            )
            libraries[constraint] = ":" + filename
        native.alias(name = project["name"], actual = select(libraries), testonly = project == benchmark, visibility = ["//visibility:public"])
    truffle_resource("TRUFFLE_NFI_RESOURCES", ":" + name, platforms, destination, file_list, hash_entry)
    _directory(
        name = "TRUFFLE_TEST_NATIVE",
        libs = [":" + p["name"] for p in projects if p["name"] != name],
        testonly = True,
        visibility = ["//visibility:public"],
    )
