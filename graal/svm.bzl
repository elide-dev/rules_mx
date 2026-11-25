"""Build the SubstrateVM driver configuration from the Bazel module graph."""

load("@rules_cc//cc:cc_binary.bzl", "cc_binary")
load("@rules_cc//cc:cc_library.bzl", "cc_library")
load("@rules_cc//cc:find_cc_toolchain.bzl", "find_cc_toolchain", "use_cc_toolchain")
load("@rules_java//java/common:java_common.bzl", "java_common")
load("@rules_java//java/common:java_info.bzl", "JavaInfo")
load("@rules_mx//mx:jar.bzl", "MxJarInfo")
load("@rules_python//python:py_binary.bzl", "py_binary")
load(":layout.bzl", "mx_archive_layout", "static_archive")

def _compiler_flags_impl(ctx):
    runtime = ctx.attr._jdk[java_common.JavaRuntimeInfo]
    versions = sorted({21: True, runtime.version: True}.keys())
    flags = [
        "-XX:+UnlockExperimentalVMOptions",
        "-XX:+EnableJVMCI",
        "-Dgraalvm.locatorDisabled=true",
        "-Dtruffle.TruffleRuntime=com.oracle.truffle.api.impl.DefaultTruffleRuntime",
        "-Dgraalvm.ForcePolyglotInvalid=true",
    ]
    if ctx.attr.linux:
        flags.append("-Dsubstratevm.HostLibC=glibc")
    flags.extend(sorted(ctx.attr.builder[MxJarInfo].required_exports.to_list()))

    jar = ctx.actions.declare_file(ctx.label.name + ".jar")
    args = ctx.actions.args()
    args.add(jar)
    files = {}
    for version in versions + ["versions"]:
        name = "graal-compiler-flags-%s.config" % version
        output = ctx.actions.declare_file(ctx.label.name + "/" + name)
        content = "\n".join([str(v) for v in versions]) if version == "versions" else "\n".join(flags)
        ctx.actions.write(output, content)
        files[name] = output
    for name, file in files.items():
        args.add(name)
        args.add(file)
    ctx.actions.run(
        executable = ctx.executable._pack,
        arguments = [args],
        inputs = files.values(),
        outputs = [jar],
        mnemonic = "SvmCompilerFlags",
    )
    return [DefaultInfo(files = depset([jar])), JavaInfo(output_jar = jar, compile_jar = None)]

svm_compiler_flags = rule(
    implementation = _compiler_flags_impl,
    attrs = {
        "builder": attr.label(mandatory = True, providers = [MxJarInfo]),
        "linux": attr.bool(),
        "_jdk": attr.label(default = Label("//tools:labs_runtime")),
        "_pack": attr.label(default = Label("//mx:pack_layout"), cfg = "exec", executable = True),
    },
)

def _jvm_fallbacks_impl(ctx):
    runtime = ctx.attr._jdk[java_common.JavaRuntimeInfo]
    root = runtime.java_home + "/lib/static/" + ctx.attr.platform + "/"
    if ctx.attr.platform.startswith("linux-"):
        root += "glibc/"
    archives = sorted([f.path for f in runtime.files.to_list() if f.path.startswith(root) and f.extension == "a"])
    if not archives:
        fail("The Labs JDK must provide static archives at " + root)
    inputs = [f for f in runtime.files.to_list() if f.path.startswith(root) and f.extension == "a"]
    output = ctx.actions.declare_file(ctx.label.name + ".c")
    args = ctx.actions.args()
    args.add_all([output, ctx.file._nm, ctx.file.implementation, ctx.file.metadata])
    args.add_all(archives)
    ctx.actions.run(
        executable = ctx.executable._generator,
        arguments = [args],
        inputs = inputs + [ctx.file.implementation, ctx.file.metadata],
        tools = [ctx.file._nm],
        outputs = [output],
        mnemonic = "SvmJvmFallbacks",
    )
    return [DefaultInfo(files = depset([output]))]

svm_jvm_fallbacks = rule(
    implementation = _jvm_fallbacks_impl,
    attrs = {
        "implementation": attr.label(mandatory = True, allow_single_file = [".c"]),
        "metadata": attr.label(mandatory = True, allow_single_file = [".py"]),
        "platform": attr.string(mandatory = True),
        "_jdk": attr.label(default = Label("//tools:labs_runtime")),
        "_nm": attr.label(default = Label("//tools/cc:llvm_nm"), cfg = "exec", allow_single_file = True),
        "_generator": attr.label(default = Label("//graal:jvm_fallbacks"), cfg = "exec", executable = True),
    },
)

def _source_root(name):
    return "src/com.oracle.svm.native." + name

def svm_native_projects(projects):
    """Lower the native builder helpers to target C/C++ libraries and mx archive paths."""
    linux = "@platforms//os:linux"
    macos = "@platforms//os:macos"
    platform = {}
    for name, os, cpu in [
        ("darwin-aarch64", macos, "aarch64"),
        ("linux-aarch64", linux, "aarch64"),
        ("linux-amd64", linux, "x86_64"),
    ]:
        setting = "svm." + name
        native.config_setting(name = setting, constraint_values = [os, "@platforms//cpu:" + cpu])
        platform[":" + setting] = name

    svm_jvm_fallbacks(
        name = "svm-jvmfuncs-fallback-builder",
        implementation = _source_root("jvm.posix") + "/src/JvmFuncs.c",
        metadata = "mx.substratevm/mx_substratevm.py",
        platform = select(platform),
    )

    archive_paths = {}
    for short, options in projects.items():
        name = "com.oracle.svm.native." + short
        root = _source_root(short)
        sources = native.glob([root + "/src/**/*.c", root + "/src/**/*.cpp"], allow_empty = True)
        headers = native.glob([root + "/src/**/*.h", root + "/src/**/*.hpp", root + "/include/*.h"], allow_empty = True)
        if not sources:
            fail("No sources for " + name)
        supported = [os for os, key in [(linux, "linux"), (macos, "darwin")] if key in options["cflags"]]
        compatible = select({os: [] for os in supported} | {"//conditions:default": ["@platforms//:incompatible"]})
        copts = select({
            os: options["cflags"][key]
            for os, key in [(linux, "linux"), (macos, "darwin")]
            if key in options["cflags"]
        })
        if short == "darwin":
            # The pinned Mach-O linker cannot synthesize Clang's Objective-C call stubs.
            copts += ["-fno-objc-msgsend-class-selector-stubs", "-fno-objc-msgsend-selector-stubs"]
        deps = ["@rules_java//toolchains:jni"] if options["jni"] else []
        if short == "reporterchelper":
            for os, suffix in [(linux, ".so"), (macos, ".dylib")]:
                cc_binary(
                    name = "lib" + options["deliverable"] + suffix,
                    srcs = sources,
                    copts = copts,
                    linkshared = True,
                    linkstatic = True,
                    target_compatible_with = [os],
                    deps = deps,
                )
            native.alias(
                name = name,
                actual = select({linux: ":libreporterchelper.so", macos: ":libreporterchelper.dylib"}),
                target_compatible_with = compatible,
                visibility = ["//visibility:public"],
            )
            continue

        cc_library(
            name = name,
            srcs = sources + ([":svm-jvmfuncs-fallback-builder"] if short == "jvm.posix" else []),
            hdrs = headers,
            copts = copts,
            includes = [root + "/include"] + options["includes"],
            linkstatic = True,
            target_compatible_with = compatible,
            deps = deps,
            visibility = ["//visibility:public"],
        )
        target = name + ".archive"
        static_archive(name = target, library = ":" + name, target_compatible_with = compatible)
        archive_paths[short] = (":" + target, "lib" + options["deliverable"] + ".a")

    common_headers = {
        source: "include/" + source.split("/")[-1]
        for source in native.glob([
            "debug/include/*.h",
            _source_root("libchelper") + "/include/*.h",
        ])
    }
    common_headers["//substratevm/src/com.oracle.svm.core/src/com/oracle/svm/core/gc/shared/include:com.oracle.svm.core.resources"] = "include/sharedGCStructs.h"
    members = {}
    for setting, target in platform.items():
        glibc = target.startswith("linux-")
        prefix = target + ("/glibc/" if glibc else "/")
        selected = ["libchelper", "jvm.posix", "libcontainer" if glibc else "darwin"]
        members[setting] = common_headers | {
            archive_paths[short][0]: prefix + archive_paths[short][1]
            for short in selected
        }
    mx_archive_layout(
        name = "SVM_HOSTED_NATIVE",
        format = "tar",
        members = select(members),
        target_compatible_with = select({setting: [] for setting in platform} | {"//conditions:default": ["@platforms//:incompatible"]}),
        visibility = ["//visibility:public"],
    )

def _runfile(ctx, artifact):
    return artifact.short_path[3:] if artifact.short_path.startswith("../") else ctx.workspace_name + "/" + artifact.short_path

def _native_image_root_impl(ctx):
    runtime = ctx.attr._jdk[java_common.JavaRuntimeInfo]
    cc = find_cc_toolchain(ctx)
    [compiler] = [f for f in cc.all_files.to_list() if f.path == cc.compiler_executable]
    driver = ctx.attr.driver
    jar_paths = {
        "GRAAL.jar": "lib/jvmci/graal.jar",
        "GRAAL_MANAGEMENT.jar": "lib/jvmci/graal-management.jar",
        "COLLECTIONS.jar": "lib/jvmci/collections.jar",
        "WORD.jar": "lib/jvmci/word.jar",
        "NATIVEIMAGE.jar": "lib/jvmci/nativeimage.jar",
        "NATIVEIMAGE_LIBGRAAL.jar": "lib/jvmci/nativeimage-libgraal.jar",
        "TRUFFLE_COMPILER.jar": "lib/truffle/truffle-compiler.jar",
    }
    entries = {}
    for jar in depset(transitive = [
        target[JavaInfo].transitive_runtime_jars
        for target in [driver, ctx.attr.management]
    ]).to_list():
        entries[jar_paths.get(jar.basename, "lib/svm/builder/" + jar.basename.lower())] = jar
    java_home = runtime.java_home + "/"
    for jdk_file in runtime.files.to_list():
        if jdk_file.path.startswith(java_home):
            entries.setdefault(jdk_file.path.removeprefix(java_home), jdk_file)
    if "lib/modules" not in entries:
        fail("The Labs JDK must provide lib/modules")
    for target, name in ctx.attr.guest.items():
        [jar] = target.files.to_list()
        entries["lib/svm/" + name + ".jar"] = jar
    entries["lib/svm/builder/lib/" + ctx.file.reporter.basename] = ctx.file.reporter
    flags = ctx.actions.declare_file(ctx.label.name + ".exports")
    exports = sorted(driver[MxJarInfo].required_exports.to_list())
    ctx.actions.write(flags, "\n".join(exports) + "\n")
    entries["lib/svm/builder/driver-exports"] = flags

    root = ctx.actions.declare_directory(ctx.label.name + ".distribution")
    args = ctx.actions.args()
    args.add(root.path)
    args.add(ctx.file.native_archive)
    for path in sorted(entries):
        args.add(path)
        args.add(entries[path])
    ctx.actions.run(
        executable = ctx.executable._assemble,
        inputs = entries.values() + [ctx.file.native_archive],
        outputs = [root],
        arguments = [args],
        mnemonic = "SvmAssembly",
    )
    ctx.actions.expand_template(
        template = ctx.file._template,
        output = ctx.outputs.out,
        substitutions = {
            "{SVM_ROOT}": repr(_runfile(ctx, root)),
            "{CC}": repr(_runfile(ctx, compiler)),
        },
    )
    return [
        DefaultInfo(
            files = depset([ctx.outputs.out]),
            runfiles = ctx.runfiles(transitive_files = depset([root], transitive = [cc.all_files])),
        ),
        OutputGroupInfo(distribution = depset([root])),
    ]

_native_image_root = rule(
    implementation = _native_image_root_impl,
    attrs = {
        "out": attr.output(mandatory = True),
        "driver": attr.label(providers = [JavaInfo, MxJarInfo], mandatory = True),
        "management": attr.label(providers = [JavaInfo], mandatory = True),
        "guest": attr.label_keyed_string_dict(providers = [JavaInfo], mandatory = True),
        "reporter": attr.label(allow_single_file = True, mandatory = True),
        "native_archive": attr.label(allow_single_file = [".tar"], mandatory = True),
        "_jdk": attr.label(default = Label("//tools:labs_runtime")),
        "_assemble": attr.label(default = Label("//graal:assemble_svm"), executable = True, cfg = "exec"),
        "_template": attr.label(default = Label("//graal:source_native_image.py.tpl"), allow_single_file = True),
    },
    fragments = ["cpp"],
    toolchains = use_cc_toolchain(),
)

def svm_launcher(name):
    _native_image_root(
        name = name + ".root",
        out = name + ".py",
        driver = ":SVM_DRIVER",
        management = "//compiler:GRAAL_MANAGEMENT",
        guest = {
            ":LIBRARY_SUPPORT": "library-support",
            ":SVM_GUEST": "svm-guest",
            ":SVM_GUEST_STAGING": "svm-guest-staging",
            ":SVM_SHARED": "svm-shared",
            ":SVM_JVMCI_GUEST": "svm-jvmci-guest",
            ":SVM_JVMCI_GUEST_STAGING": "svm-jvmci-guest-staging",
            ":SVM_JVMCI_SHARED": "svm-jvmci-shared",
            "//sdk:VMACCESS_GUEST": "vmaccess-guest",
        },
        reporter = ":com.oracle.svm.native.reporterchelper",
        native_archive = ":SVM_HOSTED_NATIVE",
    )
    py_binary(
        name = name,
        srcs = [":" + name + ".root"],
        main = name + ".py",
        data = [":" + name + ".root"],
        visibility = ["//visibility:public"],
    )
