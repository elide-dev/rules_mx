"""Compile mx DefaultNativeProject shared libraries with the registered C/C++ toolchain."""

load("@rules_cc//cc:cc_binary.bzl", "cc_binary")
load("@rules_cc//cc:cc_library.bzl", "cc_library")
load("@rules_cc//cc/common:cc_common.bzl", "cc_common")
load("@rules_cc//cc/common:cc_info.bzl", "CcInfo")
load("//mx:project_files.bzl", "mx_project_files")

def _jdk_link_inputs(ctx):
    names = {"lib" + name + ".a": True for name in ctx.attr.libraries}
    libs = [f for f in ctx.files.jdk if f.basename in names and f.dirname.endswith("/lib/static/" + ctx.attr.platform)]
    if not libs:
        libs = [f for f in ctx.files.jdk if f.basename in names and f.dirname.endswith("/lib")]
    if not libs:
        fail("The selected JDK has no static archives for " + ctx.attr.platform)
    by_name = {f.basename: f for f in libs}
    linker = cc_common.create_linker_input(
        owner = ctx.label,
        user_link_flags = depset(["-Wl,-force_load," + by_name[name].path for name in sorted(by_name)]),
        additional_inputs = depset(libs),
    )
    return [CcInfo(linking_context = cc_common.create_linking_context(linker_inputs = depset([linker])))]

_jdk_libraries = rule(
    implementation = _jdk_link_inputs,
    attrs = {
        "libraries": attr.string_list(mandatory = True),
        "platform": attr.string(mandatory = True),
        "jdk": attr.label(default = Label("//tools:labs_runtime")),
    },
)

def _jni_headers_impl(ctx):
    tree = ctx.actions.declare_directory(ctx.label.name + ".includes")
    args = ctx.actions.args()
    args.add_all(["x", ctx.file.jar, "-d", tree.path])
    ctx.actions.run(executable = ctx.executable._zipper, arguments = [args], inputs = [ctx.file.jar], outputs = [tree], mnemonic = "MxJniHeaders")
    return [
        DefaultInfo(files = depset([tree])),
        CcInfo(compilation_context = cc_common.create_compilation_context(headers = depset([tree]), includes = depset([tree.path]))),
    ]

_jni_headers = rule(
    implementation = _jni_headers_impl,
    attrs = {
        "jar": attr.label(allow_single_file = True, mandatory = True),
        "_zipper": attr.label(default = "@bazel_tools//tools/zip:zipper", cfg = "exec", executable = True),
    },
)

mx_jni_header_tree = _jni_headers

def mx_native_shared_project(name, srcs, headers, root, profiles, deps = [], jni = [], use_jdk_headers = False, jdk_static_libraries = [], testonly = False, visibility = None):
    public = [header for header in headers if header.startswith(root + "/include/")]
    private = [header for header in headers if header not in public]
    cc_library(
        name = name + ".headers",
        hdrs = public,
        strip_include_prefix = root + "/include",
        testonly = testonly,
        visibility = visibility,
    )
    jni_deps = ["@rules_java//toolchains:jni"] if use_jdk_headers else []
    for jar in jni:
        target = name + ".jni/" + jar.removeprefix(":")
        _jni_headers(name = target, jar = jar, testonly = testonly)
        jni_deps.append(":" + target)
    binaries = {}
    variants = {}
    for profile, recipe in profiles.items():
        condition = Label("//tools/cc:profile_" + profile)
        binary = name + "/" + profile + "/" + recipe["filename"]
        binaries[condition] = ":" + binary
        linkdeps = [dep + ".headers" for dep in deps] + jni_deps
        if jdk_static_libraries:
            linker = name + "/" + profile + "/jdk-static"
            _jdk_libraries(name = linker, libraries = jdk_static_libraries, platform = recipe["member"].split("/")[0], testonly = testonly)
            linkdeps.append(":" + linker)
        cc_binary(
            name = binary,
            srcs = srcs + private,
            copts = recipe["copts"],
            deps = [":" + name + ".headers"] + linkdeps,
            additional_linker_inputs = recipe["link_inputs"],
            linkopts = (["-Wl,-undefined,dynamic_lookup"] if profile == "macos_arm64" else []) + recipe["linkopts"],
            includes = sorted({header.rpartition("/")[0]: True for header in private}),
            linkshared = True,
            linkstatic = True,
            target_compatible_with = select({condition: [], "//conditions:default": ["@platforms//:incompatible"]}),
            testonly = testonly,
        )
        variants[condition] = {":" + binary: recipe["member"]} | {h: h.removeprefix(root + "/") for h in public}
    native.filegroup(
        name = name + ".library",
        srcs = select({condition: [label] for condition, label in binaries.items()} | {"//conditions:default": []}),
        target_compatible_with = select({condition: [] for condition in binaries} | {"//conditions:default": ["@platforms//:incompatible"]}),
        visibility = visibility,
        testonly = testonly,
    )
    mx_project_files(
        name = name,
        primary = select({Label("//tools/cc:profile_" + profile): recipe["member"] for profile, recipe in profiles.items()} | {"//conditions:default": ""}),
        members = select(variants | {"//conditions:default": {}}),
        target_compatible_with = select({k: [] for k in variants} | {"//conditions:default": ["@platforms//:incompatible"]}),
        testonly = testonly,
        visibility = visibility,
    )
