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
    probe = ctx.file.probe
    ctx.actions.expand_template(
        template = ctx.file._template,
        output = ctx.outputs.out,
        substitutions = {
            "{JAVA}": repr(_runfile(ctx, binaries[0])),
            "{MODULES}": repr([_runfile(ctx, jar) for jar in modules.to_list()]),
            "{JAVA_VERSION}": str(runtime.version),
            "{COMPILER}": repr(_runfile(ctx, ctx.file.compiler)) if ctx.attr.compiler else "None",
            "{PROBE}": repr(_runfile(ctx, probe)) if probe else "None",
        },
    )
    return [DefaultInfo(files = depset([ctx.outputs.out]), runfiles = ctx.runfiles(transitive_files = depset([probe] if probe else [], transitive = [modules, runtime.files])))]

_sl_main = rule(
    implementation = _sl_main_impl,
    attrs = {
        "compiler": attr.label(allow_single_file = [".jar"], providers = [JavaInfo]),
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

def sl_fixture_tests(directory):
    fixtures = native.glob([directory + "/**/*.sl"])
    inputs = native.glob([directory + "/**/*.input"], allow_empty = True)
    for fixture in fixtures:
        stem = fixture[:-3]
        expected = stem + ".output"
        stdin = stem + ".input"
        files = [fixture, expected] + ([stdin] if stdin in inputs else [])
        py_test(
            name = "sl.test." + stem.removeprefix(directory + "/").replace("/", "."),
            srcs = [Label("//graal:sl_fixture_test.py")],
            main = Label("//graal:sl_fixture_test.py"),
            args = ["$(rootpath :sl)"] + ["$(rootpath %s)" % f for f in files],
            data = [":sl"] + files,
            size = "small",
        )
