"""Build GraalPy resources and assemble its JVM standalone."""

load("@rules_cc//cc:cc_binary.bzl", "cc_binary")
load("@rules_foreign_cc//foreign_cc:defs.bzl", "cmake")
load("@rules_java//java/common:java_common.bzl", "java_common")
load("@rules_java//java/common:java_info.bzl", "JavaInfo")
load("//mx:graph.bzl", "mx_resource_layout")
load("//mx:cmake_project.bzl", "mx_cmake_output")
load("//mx:native_project.bzl", "mx_jni_header_tree")
load(":layout.bzl", "mx_archive_layout", "mx_release_archive")
load(":layout_info.bzl", "MxArchiveLayoutInfo")

_RESOURCE_ROOT = "META-INF/resources/"

def _runtime_jars(deps):
    return depset(transitive = [target[JavaInfo].transitive_runtime_jars for target in deps]).to_list()

def _freeze_impl(ctx):
    runtime = ctx.attr._jdk[java_common.JavaRuntimeInfo]
    jars = _runtime_jars(ctx.attr.runtime)
    [index] = [file for file in ctx.files.index if file.basename == "FrozenModules.java"]
    jar = ctx.actions.declare_file(ctx.label.name + ".jar")
    args = ctx.actions.args()
    args.add("--java", runtime.java_executable_exec_path)
    args.add_joined("--classpath", jars, join_with = ":")
    args.add("--launcher", ctx.attr.launcher)
    args.add("--script", ctx.file.script)
    args.add("--stdlib", ctx.file.stdlib)
    args.add("--index", index)
    args.add("--output", jar)
    ctx.actions.run(
        executable = ctx.executable._freeze,
        inputs = depset(
            ctx.files.srcs + jars + [ctx.file.script, ctx.file.stdlib, index],
            transitive = [runtime.files],
        ),
        outputs = [jar],
        arguments = [args],
        mnemonic = "GraalPyFreezeModules",
    )
    return [
        DefaultInfo(files = depset([jar])),
        JavaInfo(output_jar = jar, compile_jar = None),
    ]

graalpy_frozen_modules = rule(
    implementation = _freeze_impl,
    attrs = {
        "runtime": attr.label_list(providers = [JavaInfo], cfg = "exec"),
        "launcher": attr.string(mandatory = True),
        "script": attr.label(allow_single_file = [".py"], mandatory = True),
        "stdlib": attr.label(allow_single_file = [".py"], mandatory = True),
        "index": attr.label(mandatory = True),
        "srcs": attr.label_list(allow_files = True),
        "_jdk": attr.label(default = Label("//tools:labs_runtime"), cfg = "exec"),
        "_freeze": attr.label(default = Label(":graalpy_freeze"), cfg = "exec", executable = True),
    },
)

def _layout_runtime_impl(ctx):
    entries = {}
    jars = _runtime_jars(ctx.attr.deps)
    for jar in jars:
        path = "modules/" + jar.basename
        if path in entries:
            fail("GraalPy JVM classpath has duplicate file " + path)
        entries[path] = jar
    cp = ":".join(['$root/' + path for path in entries])
    script = ctx.actions.declare_file(ctx.label.name + "/graalpy")
    ctx.actions.write(
        script,
        """#!/bin/sh
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
exec "$root/jvm/bin/java" --enable-native-access=ALL-UNNAMED \\
    -Dorg.graalvm.launcher.executablename="$0" \\
    -cp "%s" %s %s --python.PythonHome="$root" "$@"
""" % (cp, ctx.attr.launcher, " ".join(ctx.attr.args)),
        is_executable = True,
    )
    for path in ctx.attr.launchers:
        entries[path] = script
    return [MxArchiveLayoutInfo(archive = None, entries = entries, modes = {path: "755" for path in ctx.attr.launchers})]

_runtime_layout = rule(
    implementation = _layout_runtime_impl,
    attrs = {
        "deps": attr.label_list(providers = [JavaInfo]),
        "launcher": attr.string(mandatory = True),
        "launchers": attr.string_list(mandatory = True),
        "args": attr.string_list(),
    },
)

def graalpy_jvm_standalone(runtime, main_class, launchers, args, licenses, jdk_exclude, prefix):
    _runtime_layout(
        name = "py_jvm_runtime",
        deps = runtime,
        args = args,
        launcher = main_class,
        launchers = launchers,
    )
    mx_archive_layout(
        name = "GRAALPY_JVM_STANDALONE",
        format = "dir",
        members = licenses | {Label("//graal:standalone_jdk_release"): "release"},
        archive_imports = {
            ":py_install_home": "=",
            ":py_jvm_runtime": "=",
        },
        jdk_homes = {
            Label("//graal:standalone_jdk_archive"): json.encode({
                "directory": "jvm/",
                "exclude": jdk_exclude,
            }),
        },
        use_param_file = True,
        visibility = ["//visibility:public"],
    )
    mx_release_archive(
        name = "GRAALPY_JVM_STANDALONE_RELEASE_ARCHIVE",
        install = ":GRAALPY_JVM_STANDALONE",
        prefix = prefix,
        use_param_file = True,
        visibility = ["//visibility:public"],
    )

def _resource_layout(name, category, members, literals = {}):
    mx_archive_layout(
        name = name,
        format = "dir",
        members = members,
        literals = literals,
        file_list_entry = _RESOURCE_ROOT + category + ".files",
        hash_entry = _RESOURCE_ROOT + category + ".sha256",
        use_param_file = True,
    )

def _cmake_files(name, sources, data, config, outputs, targets, compatible):
    native.filegroup(name = name + "_sources", srcs = sources)
    cmake(
        name = name,
        lib_source = ":" + name + "_sources",
        build_data = data,
        cache_entries = config,
        generate_args = ["-G Ninja"],
        targets = targets,
        install = False,
        postfix_script = "\n".join([
            'mkdir -p "$$INSTALLDIR/%s" && cp -L "%s" "$$INSTALLDIR/%s"' % (file.rpartition("/")[0] or ".", file, file)
            for file in outputs
        ]),
        out_include_dir = "",
        out_data_files = outputs,
        target_compatible_with = compatible,
    )

def _native_extensions(profile, os, arch, compatible, soabi, abiflags, extension, multiarch, python_version, graal_version_full, graal_version_hex, pyconfig_srcs, cext_srcs, cext_results):
    sysconfig = "_sysconfigdata_" + abiflags + "_" + os + "_" + multiarch + ".py"
    config = "pyconfig_" + profile
    _cmake_files(
        config,
        pyconfig_srcs,
        [],
        {
            "GRAALPY_VERSION": graal_version_full,
            "GRAALPY_VERSION_NUM": graal_version_hex,
            "GRAALPY_SYSCONFIGDATA_NAME": sysconfig.removesuffix(".py"),
            "GRAALPY_SYSCONFIG_ABIFLAGS": abiflags,
            "GRAALPY_SYSCONFIG_EXT_SUFFIX": "." + soabi + ".so",
            "GRAALPY_SYSCONFIG_GIL_DISABLED": "0",
            "GRAALPY_SYSCONFIG_MULTIARCH": multiarch,
            "GRAALPY_SYSCONFIG_SOABI": soabi,
            "GRAALPY_SYSCONFIG_VERSION": python_version,
        },
        ["pyconfig.h", sysconfig],
        ["all"],
        compatible,
    )
    header = config + "/pyconfig.h"
    mx_cmake_output(name = header, project = ":" + config, member = "pyconfig.h")
    sysconfig_target = config + "/" + sysconfig
    mx_cmake_output(name = sysconfig_target, project = ":" + config, member = sysconfig)
    include = config + "_include"
    mx_archive_layout(name = include, format = "dir", members = {":" + header: "pyconfig.h"})

    ffi = "py_ffi_" + profile
    libc = "default" if os == "darwin" else "glibc"
    mx_archive_layout(
        name = ffi,
        format = "dir",
        members = {},
        archive_imports = {"//truffle:LIBFFI_DIST": os + "-" + arch + "/" + libc + "/*="},
    )
    cext = "py_cext_" + profile
    outputs = [
        path.replace("<lib:python-native>", "libpython-native" + extension).replace("<graalpy_ext>", "." + soabi + ".so")
        for path in cext_results
    ]
    _cmake_files(
        cext,
        cext_srcs,
        [":py_capi_headers", ":" + include, ":" + ffi],
        {
            "CMAKE_BUILD_TYPE": "Release",
            "CAPI_INC_DIR": "$$EXT_BUILD_ROOT$$/$(execpath :py_capi_headers)",
            "PYCONFIG_INCLUDE_DIR": "$$EXT_BUILD_ROOT$$/$(execpath :%s)" % include,
            "LIBFFI_DIST": "$$EXT_BUILD_ROOT$$/$(execpath :%s)" % ffi,
            "GRAALPY_PARENT_DIR": "$$EXT_BUILD_ROOT$$",
            "GRAALPY_EXT": "." + soabi + ".so",
        },
        outputs,
        ["install"],
        compatible,
    )
    artifacts = {}
    for member in outputs:
        target = cext + "/" + member
        mx_cmake_output(name = target, project = ":" + cext, member = member)
        artifacts[":" + target] = member.removeprefix("bin/")
    return artifacts, ":" + header, ":" + sysconfig_target, sysconfig

def graalpy_resources(version, abi, abiflags, graal_version, python_version, libraries, headers, zsupport_srcs, zsupport_copts, posix_srcs, posix_copts, posix_linkopts, graal_version_full, graal_version_hex, pyconfig_srcs, cext_srcs, cext_results):
    """Package the inputs read by GraalPy's upstream InternalResource provider."""
    resource_jars = []
    for category, files in libraries.items():
        name = "py_resource_" + category
        _resource_layout(name, category, {
            source: _RESOURCE_ROOT + category + "/" + path
            for source, path in files.items()
        })
        resource_jars.append(":" + name)

    _resource_layout("py_resource_headers", "include", {
        source: _RESOURCE_ROOT + "include/" + path
        for source, path in headers.items()
    })
    _resource_layout("py_resource_ni", "ni", {
        "mx.graalpython/native-image.properties": _RESOURCE_ROOT + "native-image.properties",
    })

    native_versions = {}
    native_libs = {}
    install_homes = {}
    language_versions = {}
    mx_jni_header_tree(name = "py_capi_headers", jar = ":com.oracle.graal.python.jni_headers")
    for profile, os, arch, machine, extension in [
        ("linux_amd64", "linux", "amd64", "x86_64", ".so"),
        ("linux_arm64", "linux", "aarch64", "aarch64", ".so"),
        ("macos_arm64", "darwin", "aarch64", "aarch64", ".dylib"),
    ]:
        condition = Label("//tools/cc:profile_" + profile)
        compatible = select({condition: [], "//conditions:default": ["@platforms//:incompatible"]})
        platform = os + "/" + arch
        multiarch = machine + "-" + os
        soabi = abi + abiflags + "-native-" + multiarch
        version_contents = "\n".join([version, abiflags, soabi, "." + soabi + ".so", multiarch, ""])
        binary = "py_zsupport/" + profile + "/libzsupport" + extension
        cc_binary(
            name = binary,
            srcs = zsupport_srcs,
            copts = zsupport_copts,
            deps = [Label("@zlib//:zlib")],
            linkshared = True,
            linkstatic = True,
            target_compatible_with = compatible,
        )
        posix = "py_posix/" + profile + "/libposix" + extension
        cc_binary(
            name = posix,
            srcs = posix_srcs,
            copts = posix_copts[os],
            linkopts = posix_linkopts[os],
            linkshared = True,
            linkstatic = True,
            target_compatible_with = compatible,
        )
        manifest = "py_versions_" + profile
        _resource_layout(manifest, platform + "/versions", {}, {
            _RESOURCE_ROOT + platform + "/graalpy_versions": version_contents,
        })
        libs = "py_native_" + profile
        native_home = "lib/graalpy" + graal_version + "/"
        native_root = _RESOURCE_ROOT + platform + "/"
        _resource_layout(libs, platform + "/native", {
            ":" + binary: native_root + native_home + "libzsupport" + extension,
            ":" + posix: native_root + native_home + "libposix" + extension,
        })
        home = "py_install_home_" + profile
        extensions, pyconfig, sysconfig_file, sysconfig = _native_extensions(
            profile, os, arch, compatible, soabi, abiflags, extension, multiarch,
            python_version, graal_version_full, graal_version_hex,
            pyconfig_srcs, cext_srcs, cext_results,
        )
        mx_archive_layout(
            name = home,
            format = "dir",
            members = {target: native_home + path for target, path in extensions.items()} | {
                pyconfig: "include/python" + python_version + abiflags + "/pyconfig.h",
                sysconfig_file: "lib/python" + python_version + "/" + sysconfig,
            },
            archive_imports = {
                ":py_resource_libpython": _RESOURCE_ROOT + "libpython/*=lib/python" + python_version + "/",
                ":py_resource_libgraalpy": _RESOURCE_ROOT + "libgraalpy/*=" + native_home,
                ":py_resource_headers": _RESOURCE_ROOT + "include/*=include/python" + python_version + abiflags + "/",
                ":py_resource_ni": _RESOURCE_ROOT + "native-image.properties=native-image.properties",
                ":" + libs: native_root + native_home + "*=" + native_home,
            },
            use_param_file = True,
        )
        language_version = "py_version_" + profile
        mx_resource_layout(name = language_version, literals = {"graalpy_versions": version_contents})
        native_versions[condition] = ":" + manifest
        native_libs[condition] = ":" + libs
        language_versions[condition] = ":" + language_version
        install_homes[condition] = ":" + home

    native.alias(name = "py_install_home", actual = select(install_homes))
    native.alias(name = "py_version", actual = select(language_versions))
    native.alias(
        name = "py_native_resources",
        actual = select(native_libs),
    )
    # Expose JavaInfo for each resource JAR to the launcher.
    native.alias(
        name = "py_platform_versions",
        actual = select(native_versions),
    )
    return resource_jars + [
        ":py_version",
        ":py_platform_versions",
        ":py_native_resources",
        ":py_resource_headers",
        ":py_resource_ni",
    ]
