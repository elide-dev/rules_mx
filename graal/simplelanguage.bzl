"""Launch packaged SimpleLanguage modules and run upstream guest programs."""

load("@rules_java//java/common:java_common.bzl", "java_common")
load("@rules_java//java/common:java_info.bzl", "JavaInfo")
load("@rules_python//python:py_binary.bzl", "py_binary")
load("@rules_python//python:py_test.bzl", "py_test")

_RUNTIME = "@bazel_tools//tools/jdk:runtime_toolchain_type"

def _runfile(ctx, f):
    return f.short_path[3:] if f.short_path.startswith("../") else ctx.workspace_name + "/" + f.short_path

def _sl_main_impl(ctx):
    runtime = ctx.attr.labs_runtime[java_common.JavaRuntimeInfo] if ctx.attr.compiler else ctx.toolchains[_RUNTIME].java_runtime
    binaries = [f for f in runtime.files.to_list() if f.path == runtime.java_executable_exec_path]
    if len(binaries) != 1:
        fail("SimpleLanguage requires a file-backed Java runtime")
    modules = depset(transitive = [dep[JavaInfo].transitive_runtime_jars for dep in ctx.attr.modules])
    if ctx.attr.compiler:
        modules = depset(transitive = [modules, ctx.attr.compiler[JavaInfo].transitive_runtime_jars])
    module_jars = {jar: True for jar in modules.to_list()}
    if ctx.file.compiletime:
        module_jars[ctx.file.compiletime] = True
    classpath = [jar for jar in depset(transitive = [dep[JavaInfo].transitive_runtime_jars for dep in ctx.attr.test_classpath]).to_list() if jar not in module_jars]
    probe = ctx.file.probe
    ctx.actions.expand_template(
        template = ctx.file._template,
        output = ctx.outputs.out,
        substitutions = {
            "{HSDIS}": repr(_runfile(ctx, ctx.file.hsdis)) if ctx.file.hsdis else "None",
            "{JAVA}": repr(_runfile(ctx, binaries[0])),
            "{MODULES}": repr([_runfile(ctx, jar) for jar in modules.to_list()]),
            "{JAVA_VERSION}": str(runtime.version),
            "{COMPILER}": repr(_runfile(ctx, ctx.file.compiler)) if ctx.attr.compiler else "None",
            "{JVM_FLAGS}": repr(ctx.attr.jvm_flags),
            "{TEST_CLASS}": repr(ctx.attr.test_class),
            "{CLASSPATH}": repr([_runfile(ctx, jar) for jar in classpath]),
            "{PROBE}": repr(_runfile(ctx, probe)) if probe else "None",
        },
    )
    return [DefaultInfo(files = depset([ctx.outputs.out]), runfiles = ctx.runfiles(transitive_files = depset(classpath + ctx.files.hsdis + ([probe] if probe else []), transitive = [modules, runtime.files])))]

_sl_main = rule(
    implementation = _sl_main_impl,
    attrs = {
        "compiletime": attr.label(allow_single_file = [".jar"]),
        "hsdis": attr.label(allow_single_file = True),
        "compiler": attr.label(allow_single_file = [".jar"], providers = [JavaInfo]),
        "jvm_flags": attr.string_list(),
        "test_class": attr.string(),
        "test_classpath": attr.label_list(providers = [JavaInfo]),
        "probe": attr.label(allow_single_file = [".jar"]),
        "labs_runtime": attr.label(),
        "modules": attr.label_list(providers = [JavaInfo], mandatory = True),
        "out": attr.output(mandatory = True),
        "_template": attr.label(default = Label("//graal:sl.py.tpl"), allow_single_file = True),
    },
    toolchains = [_RUNTIME],
)

def simplelanguage(name, compiler = None, probe = None):
    modules = ["//truffle:TRUFFLE_SL", "//truffle:TRUFFLE_SL_LAUNCHER"]
    if compiler:
        modules.append("//truffle:TRUFFLE_RUNTIME")
    _sl_main(
        name = name + ".main",
        out = name + ".py",
        modules = modules,
        compiler = compiler,
        labs_runtime = Label("//tools:labs_runtime") if compiler else None,
        probe = probe,
        testonly = bool(probe),
    )
    args = dict(name = name, srcs = [":" + name + ".main"], main = name + ".py", data = [":" + name + ".main"], visibility = ["//visibility:public"])
    if probe:
        py_test(size = "medium", **args)
    else:
        py_binary(**args)

def sl_fixture_tests(package, names, inputs = [], prefix = ""):
    for name in names:
        files = [package + ":" + name + suffix for suffix in ([".sl", ".output", ".input"] if name in inputs else [".sl", ".output"])]
        py_test(
            name = "sl.test." + prefix + name,
            srcs = [Label("//graal:sl_fixture_test.py")],
            main = Label("//graal:sl_fixture_test.py"),
            args = ["$(rootpath :sl)"] + ["$(rootpath %s)" % f for f in files],
            data = [":sl"] + files,
            size = "small",
        )

def compiler_test_runner(name, jvm_flags):
    """Run compiler JUnit classes with the same runtime annotation substitution as mx."""
    _sl_main(
        name = name + ".main",
        out = name + ".py",
        modules = ["//truffle:TRUFFLE_SL", "//truffle:TRUFFLE_RUNTIME"],
        compiler = ":GRAAL",
        compiletime = ":GRAAL_TEST_COMPILETIME",
        hsdis = ":hsdis_library_file",
        labs_runtime = Label("//tools:labs_runtime"),
        test_classpath = [":GRAAL_TEST", ":GRAAL_TEST_RUNTIME", Label("//graal:compiler_junit")],
        jvm_flags = jvm_flags,
        testonly = True,
    )
    py_binary(
        name = name,
        srcs = [":" + name + ".main"],
        main = name + ".py",
        data = [":" + name + ".main"],
        testonly = True,
    )

def optimizing_junit(name, test_class, runtime_deps, jvm_flags = []):
    """Run a JUnit class alongside the packaged SL modules on the Labs JDK."""
    _sl_main(
        name = name + ".main",
        out = name + ".py",
        modules = ["//truffle:TRUFFLE_SL", "//truffle:TRUFFLE_RUNTIME"],
        compiler = "//compiler:GRAAL",
        labs_runtime = Label("//tools:labs_runtime"),
        test_classpath = runtime_deps + ["@bazel_tools//tools/jdk:TestRunner"],
        jvm_flags = jvm_flags,
        test_class = test_class,
        testonly = True,
    )
    py_test(
        name = name,
        main = name + ".py",
        srcs = [":" + name + ".main"],
        data = [":" + name + ".main"],
        size = "medium",
    )
