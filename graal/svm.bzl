"""Build the SubstrateVM driver configuration from the Bazel module graph."""

load("@rules_cc//cc:cc_binary.bzl", "cc_binary")
load("@rules_cc//cc:cc_library.bzl", "cc_library")
load("@rules_cc//cc:find_cc_toolchain.bzl", "find_cc_toolchain", "use_cc_toolchain")
load("@rules_cc//cc/common:cc_common.bzl", "cc_common")
load("@rules_cc//cc/common:cc_info.bzl", "CcInfo")
load("@rules_java//java/common:java_common.bzl", "java_common")
load("@rules_java//java/common:java_info.bzl", "JavaInfo")
load("@rules_mx//mx:jar.bzl", "MxJarInfo")
load("@rules_python//python:py_binary.bzl", "py_binary")
load("@rules_python//python:py_test.bzl", "py_test")
load(":layout.bzl", "MxArchiveLayoutInfo", "mx_archive_layout", "static_archive")

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
    extension = "lib" if ctx.attr.platform.startswith("windows-") else "a"
    inputs = [f for f in runtime.files.to_list() if f.path.startswith(root) and f.extension == extension]
    archives = sorted([f.path for f in inputs])
    if not archives:
        fail("The Labs JDK must provide static archives at " + root)
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

def _svm_jvm_symbols_impl(ctx):
    output = ctx.actions.declare_file(ctx.label.name)
    args = ctx.actions.args()
    args.add("--nm", ctx.file._nm)
    if ctx.attr.darwin:
        args.add("--darwin")
    args.add_all("--svm-jvm", [ctx.file.archive, output])
    ctx.actions.run(
        executable = ctx.executable._generator,
        arguments = [args],
        inputs = [ctx.file.archive],
        tools = [ctx.file._nm],
        outputs = [output],
        mnemonic = "SvmStaticSymbols",
    )
    return [DefaultInfo(files = depset([output]))]

svm_jvm_symbols = rule(
    implementation = _svm_jvm_symbols_impl,
    doc = "The static library symbol manifest of the SubstrateVM libjvm replacement.",
    attrs = {
        "archive": attr.label(mandatory = True, allow_single_file = [".a"]),
        "darwin": attr.bool(),
        "_nm": attr.label(default = Label("//tools/cc:llvm_nm"), cfg = "exec", allow_single_file = True),
        "_generator": attr.label(default = Label("//graal:static_symbols"), cfg = "exec", executable = True),
    },
)

def _jdk_static_symbols(ctx, runtime, platform):
    """Declares symbol manifests next to the Labs JDK's static archives, keyed by java.home path."""
    root = "lib/static/" + platform + "/"
    if platform.startswith("linux-"):
        root += "glibc/"
    home = runtime.java_home + "/"
    archives = [f for f in runtime.files.to_list() if f.path.startswith(home + root) and f.extension == "a"]
    jvm = [f for f in archives if f.basename == "libjvm.a"]
    archives = [f for f in archives if f.basename != "libjvm.a"]
    if not archives:
        fail("The Labs JDK must provide static archives at " + root)
    args = ctx.actions.args()
    args.add("--nm", ctx.file._nm)
    args.add("--objdump", ctx.file._objdump)
    args.add("--java-home", runtime.java_home)
    if platform.startswith("darwin-"):
        args.add("--darwin")
    if jvm:
        args.add("--jdk-jvm", jvm[0])
    manifests = {}
    for archive in archives:
        relative = archive.path.removeprefix(home)
        output = ctx.actions.declare_file(ctx.label.name + ".jdk_symbols/" + relative + ".symbols")
        args.add_all("--jdk", [archive, output])
        manifests[relative + ".symbols"] = output
    dynamic = [f for f in runtime.files.to_list() if f.extension in ("so", "dylib")]
    ctx.actions.run(
        executable = ctx.executable._static_symbols,
        arguments = [args],
        inputs = archives + dynamic,
        tools = [ctx.file._nm, ctx.file._objdump],
        outputs = manifests.values(),
        mnemonic = "SvmJdkStaticSymbols",
    )
    return manifests

def _source_root(name):
    return "src/com.oracle.svm.native." + name

def _labs_jni_impl(ctx):
    runtime = ctx.attr._jdk[java_common.JavaRuntimeInfo]
    include = runtime.java_home + "/include"
    paths = [include + "/jni.h", include + "/" + ctx.attr.os + "/jni_md.h"]
    headers = [file for file in runtime.files.to_list() if file.path in paths]
    if len(headers) != len(paths):
        fail("Missing JNI headers in the selected Labs JDK")
    return [CcInfo(compilation_context = cc_common.create_compilation_context(
        headers = depset(headers),
        system_includes = depset([header.dirname for header in headers]),
    ))]

_labs_jni = rule(
    implementation = _labs_jni_impl,
    attrs = {
        "os": attr.string(mandatory = True),
        "_jdk": attr.label(default = Label("//tools:labs_runtime")),
    },
)

def svm_native_projects(projects):
    """Lower the native builder helpers to target C/C++ libraries and mx archive paths."""
    linux = "@platforms//os:linux"
    macos = "@platforms//os:macos"
    windows = "@platforms//os:windows"
    systems = [(linux, "linux"), (macos, "darwin"), (windows, "windows")]
    platform = {}
    for name, os, cpu in [
        ("darwin-aarch64", macos, "aarch64"),
        ("linux-aarch64", linux, "aarch64"),
        ("linux-amd64", linux, "x86_64"),
    ]:
        setting = "svm." + name
        native.config_setting(name = setting, constraint_values = [os, "@platforms//cpu:" + cpu])
        platform[":" + setting] = name

    # Only the Windows libjvm replacement uses this setting. rules_mx pins no Windows Labs
    # JDK, so the fallbacks for Windows need one registered as //tools:labs_runtime.
    native.config_setting(name = "svm.windows-amd64", constraint_values = [windows, "@platforms//cpu:x86_64"])
    svm_jvm_fallbacks(
        name = "svm-jvmfuncs-fallback-builder",
        implementation = select({
            windows: _source_root("jvm.windows") + "/src/JvmFuncs.c",
            "//conditions:default": _source_root("jvm.posix") + "/src/JvmFuncs.c",
        }),
        metadata = "mx.substratevm/mx_substratevm.py",
        platform = select(platform | {":svm.windows-amd64": "windows-amd64"}),
    )

    _labs_jni(name = "svm-labs-jni", os = select({linux: "linux", macos: "darwin", windows: "win32"}))
    archive_paths = {}
    for short, options in projects.items():
        name = "com.oracle.svm.native." + short
        root = _source_root(short)
        sources = native.glob([root + "/src/**/*.c", root + "/src/**/*.cpp"], allow_empty = True)
        headers = native.glob([root + "/src/**/*.h", root + "/src/**/*.hpp", root + "/include/*.h"], allow_empty = True)
        if not sources:
            fail("No sources for " + name)
        supported = [os for os, key in systems if key in options["cflags"]]
        compatible = select({os: [] for os in supported} | {"//conditions:default": ["@platforms//:incompatible"]})
        copts = select({
            os: options["cflags"][key]
            for os, key in systems
            if key in options["cflags"]
        } | ({} if len(supported) == len(systems) else {"//conditions:default": []}))
        if short == "darwin":
            # The pinned Mach-O linker cannot synthesize Clang's Objective-C call stubs.
            copts += ["-fno-objc-msgsend-class-selector-stubs", "-fno-objc-msgsend-selector-stubs"]
        deps = [":svm-labs-jni"] if options["jni"] else []
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
            srcs = sources + ([":svm-jvmfuncs-fallback-builder"] if short in ("jvm.posix", "jvm.windows") else []),
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

    # Native Image reads a symbol manifest next to the libjvm replacement.
    svm_jvm_symbols(
        name = "libjvm.a.symbols",
        archive = archive_paths["jvm.posix"][0],
        darwin = select({macos: True, "//conditions:default": False}),
    )
    members = {}
    for setting, target in platform.items():
        glibc = target.startswith("linux-")
        prefix = target + ("/glibc/" if glibc else "/")
        selected = ["libchelper", "jvm.posix", "libcontainer" if glibc else "darwin"]
        if "libzstd" in projects:
            selected.append("libzstd")
        members[setting] = common_headers | {
            archive_paths[short][0]: prefix + archive_paths[short][1]
            for short in selected
        } | {":libjvm.a.symbols": prefix + archive_paths["jvm.posix"][1] + ".symbols"}
    mx_archive_layout(
        name = "SVM_HOSTED_NATIVE",
        format = "tar",
        members = select(members),
        target_compatible_with = select({setting: [] for setting in platform} | {"//conditions:default": ["@platforms//:incompatible"]}),
        visibility = ["//visibility:public"],
    )

# The builder_jar_distributions of mx's SubstrateVM component, with SVM_FOREIGN as on JDK 22
# and later.
_BUILDER_JARS = [
    ":SVM",
    ":SVM_CONFIGURE",
    ":SVM_GUEST_STAGING",
    ":SVM_SHARED",
    ":SVM_JVMCI_GUEST_STAGING",
    ":SVM_JVMCI_SHARED",
    "//espresso-shared:ESPRESSO_SVM",
    ":OBJECTFILE",
    ":POINTSTO",
    ":SVM_CAPNPROTO_RUNTIME",
    ":NATIVE_IMAGE_BASE",
    "//compiler:VMACCESS",
    "//sdk:VMACCESS_GUEST",
    "//compiler:HOSTVMACCESS",
    ":SVM_FOREIGN",
]

# Its jar_distributions, which Native Image loads for the image itself.
_GUEST_JARS = {
    ":LIBRARY_SUPPORT": "library-support",
    ":SVM_GUEST": "svm-guest",
    ":SVM_GUEST_STAGING": "svm-guest-staging",
    ":SVM_SHARED": "svm-shared",
    ":SVM_JVMCI_GUEST": "svm-jvmci-guest",
    ":SVM_JVMCI_GUEST_STAGING": "svm-jvmci-guest-staging",
    ":SVM_JVMCI_SHARED": "svm-jvmci-shared",
    "//sdk:VMACCESS_GUEST": "vmaccess-guest",
}

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
        for target in [driver, ctx.attr.management] + ctx.attr.builder
    ]).to_list():
        entries[jar_paths.get(jar.basename, "lib/svm/builder/" + jar.basename.lower())] = jar
    java_home = runtime.java_home + "/"
    for jdk_file in runtime.files.to_list():
        if jdk_file.path.startswith(java_home):
            entries.setdefault(jdk_file.path.removeprefix(java_home), jdk_file)
    if "lib/modules" not in entries:
        fail("The Labs JDK must provide lib/modules")
    entries.update(_jdk_static_symbols(ctx, runtime, ctx.attr.platform))
    for target, name in ctx.attr.guest.items():
        [jar] = target.files.to_list()
        entries["lib/svm/" + name + ".jar"] = jar
    entries["lib/svm/builder/lib/" + ctx.file.reporter.basename] = ctx.file.reporter
    for target, name in ctx.attr.builder_libs.items():
        [jar] = target[JavaInfo].runtime_output_jars
        entries["lib/svm/builder/lib/" + name] = jar
    for target, path in ctx.attr.macros.items():
        files = target[JavaInfo].runtime_output_jars if JavaInfo in target else target.files.to_list()
        if len(files) != 1:
            fail("Expected one file for the macro path " + path)
        entries["lib/svm/macros/" + path] = files[0]
    for target, path in ctx.attr.component_jars.items():
        [jar] = target[JavaInfo].runtime_output_jars
        entries[path] = jar
    for target, directory in ctx.attr.component_layouts.items():
        for member, artifact in target[MxArchiveLayoutInfo].entries.items():
            path = directory + "/" + member
            if path in entries:
                fail("Duplicate Native Image installation path " + path)
            entries[path] = artifact
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
        "builder": attr.label_list(providers = [JavaInfo]),
        "guest": attr.label_keyed_string_dict(providers = [JavaInfo], mandatory = True),
        "reporter": attr.label(allow_single_file = True, mandatory = True),
        "builder_libs": attr.label_keyed_string_dict(providers = [JavaInfo]),
        "macros": attr.label_keyed_string_dict(allow_files = True),
        "component_jars": attr.label_keyed_string_dict(providers = [JavaInfo]),
        "component_layouts": attr.label_keyed_string_dict(providers = [MxArchiveLayoutInfo]),
        "native_archive": attr.label(allow_single_file = [".tar"], mandatory = True),
        "platform": attr.string(mandatory = True),
        "_jdk": attr.label(default = Label("//tools:labs_runtime")),
        "_nm": attr.label(default = Label("//tools/cc:llvm_nm"), cfg = "exec", allow_single_file = True),
        "_objdump": attr.label(default = Label("//tools/cc:llvm_objdump"), cfg = "exec", allow_single_file = True),
        "_static_symbols": attr.label(default = Label("//graal:static_symbols"), cfg = "exec", executable = True),
        "_assemble": attr.label(default = Label("//graal:assemble_svm"), executable = True, cfg = "exec"),
        "_template": attr.label(default = Label("//graal:source_native_image.py.tpl"), allow_single_file = True),
    },
    fragments = ["cpp"],
    toolchains = use_cc_toolchain(),
)

def svm_launcher(name, builder_libs = {}, component_jars = {}, component_layouts = {}):
    """Assemble a Native Image distribution root and its launcher.

    Args:
      name: launcher name.
      builder_libs: JARs that the builder loads from lib/svm/builder/lib, keyed to their file names.
    """
    _native_image_root(
        name = name + ".root",
        out = name + ".py",
        driver = ":SVM_DRIVER",
        component_jars = component_jars,
        component_layouts = component_layouts,
        management = "//compiler:GRAAL_MANAGEMENT",
        builder = _BUILDER_JARS,
        guest = _GUEST_JARS,
        reporter = ":com.oracle.svm.native.reporterchelper",
        builder_libs = builder_libs,
        # The NATIVE_IMAGE_JUNIT_SUPPORT macro and the JARs of mx's native-image JUnit
        # component, which `--macro:junit` loads to build test images.
        macros = {
            "mx.substratevm/macro-junit.properties": "junit/native-image.properties",
            "mx.substratevm/svm-junit.packages": "junit/svm-junit.packages",
            ":JUNIT_SUPPORT": "junit/junit-support.jar",
            Label("//graal:mx_junit_tool"): "junit/junit-tool.jar",
            Label("//graal:junit"): "junit/junit.jar",
            Label("//graal:hamcrest"): "junit/hamcrest.jar",
        },
        native_archive = ":SVM_HOSTED_NATIVE",
        platform = select({":" + setting: setting.removeprefix("svm.") for setting in ["svm.darwin-aarch64", "svm.linux-aarch64", "svm.linux-amd64"]}),
        visibility = ["//visibility:public"],
    )
    py_binary(
        name = name,
        srcs = [":" + name + ".root"],
        main = name + ".py",
        data = [":" + name + ".root"],
        visibility = ["//visibility:public"],
    )

def _svm_unittest_inputs_impl(ctx):
    runtime = ctx.attr._jdk[java_common.JavaRuntimeInfo]
    [java] = [f for f in runtime.files.to_list() if f.path == runtime.java_executable_exec_path]
    tests = depset(transitive = [dep[JavaInfo].transitive_runtime_jars for dep in ctx.attr.deps])

    # The image builder already provides its own distributions, as mx's implicit_excludes do.
    provided = {jar: True for jar in depset(transitive = [dep[JavaInfo].transitive_runtime_jars for dep in ctx.attr.provided]).to_list()}
    classpath = [jar for jar in tests.to_list() if jar not in provided]
    helper = depset(transitive = [tests, ctx.attr.junit_support[JavaInfo].transitive_runtime_jars]).to_list()
    manifest = ctx.actions.declare_file(ctx.label.name + ".json")
    ctx.actions.write(manifest, json.encode({
        "classpath": [_runfile(ctx, jar) for jar in classpath],
        "helper_classpath": [_runfile(ctx, jar) for jar in helper],
        "java": _runfile(ctx, java),
    }))
    return [DefaultInfo(files = depset([manifest]), runfiles = ctx.runfiles(transitive_files = depset(helper, transitive = [runtime.files])))]

_svm_unittest_inputs = rule(
    implementation = _svm_unittest_inputs_impl,
    attrs = {
        "deps": attr.label_list(providers = [JavaInfo], mandatory = True),
        "provided": attr.label_list(providers = [JavaInfo]),
        "junit_support": attr.label(providers = [JavaInfo], mandatory = True),
        "_jdk": attr.label(default = Label("//tools:labs_runtime")),
    },
)

def svm_native_unittest(name, tests, deps, build_args = [], default_group = False, tags = [], size = "enormous"):
    """Run JUnit classes inside a source-built Native Image, as `mx native-unittest` does.

    Args:
      name: test name.
      tests: fully qualified test classes; they are grouped by @NativeImageBuildArgs.
      deps: Java targets that provide the test classes and their dependencies.
      build_args: additional image builder arguments.
      default_group: only build the classes without custom @NativeImageBuildArgs, as the
        argument-less `mx native-unittest` does.
      tags: test tags.
      size: test size.
    """
    _svm_unittest_inputs(
        name = name + ".inputs",
        deps = deps,
        provided = [":SVM_DRIVER", "//compiler:GRAAL_MANAGEMENT", ":JUNIT_SUPPORT"] + _BUILDER_JARS + [label for label in _GUEST_JARS if label not in _BUILDER_JARS],
        junit_support = ":JUNIT_SUPPORT",
        testonly = True,
    )
    py_test(
        name = name,
        srcs = [Label("//graal:svm_native_unittest.py")],
        main = Label("//graal:svm_native_unittest.py"),
        args = ["$(rootpath :native-image.root)", "$(rootpath :%s.inputs)" % name] + (["--default-group"] if default_group else []) + ["--tests"] + tests + (["--build-args"] + build_args if build_args else []),
        data = [":native-image.root", ":%s.inputs" % name],
        size = size,
        tags = tags,
        target_compatible_with = select({":" + s: [] for s in ["svm.darwin-aarch64", "svm.linux-aarch64", "svm.linux-amd64"]} | {"//conditions:default": ["@platforms//:incompatible"]}),
    )
