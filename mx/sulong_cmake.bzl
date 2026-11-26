"""Use the registered rules_foreign_cc CMake and Ninja toolchains to plan Sulong builds."""

load("@rules_foreign_cc//toolchains/native_tools:tool_access.bzl", "get_cmake_data", "get_ninja_data")

load("//mx:project_files.bzl", "mx_project_files")

def _plan_impl(ctx):
    cmake = get_cmake_data(ctx)
    ninja = get_ninja_data(ctx)
    if not cmake.target or not ninja.target:
        fail("Sulong requires file-backed CMake and Ninja execution toolchains")
    spec = ctx.actions.declare_file(ctx.label.name + ".json")
    plans = {case: out.path for case, out in zip(ctx.attr.cases, ctx.outputs.plans)}
    ctx.actions.write(spec, json.encode({
        "tool_env": cmake.env | ninja.env,
        "plans": plans,
        "source": ctx.file.cmakelists.dirname,
        "source_dir": ctx.file.source_marker.dirname if ctx.file.source_marker else "",
        "modules": ctx.file.suite_module.dirname,
        "variant": ctx.attr.variant,
        "project": ctx.attr.project,
        "config": ctx.attr.config,
        "languages": ctx.attr.languages,
        "cmake": cmake.path.removeprefix("$EXT_BUILD_ROOT/"),
        "ninja": ninja.path.removeprefix("$EXT_BUILD_ROOT/"),
        "sdk": ctx.file.sdk.path,
        "target_config": ctx.file._target_config.path,
    }))
    ctx.actions.run(
        executable = ctx.executable._runner,
        arguments = ["configure", spec.path],
        inputs = depset([spec, ctx.file.cmakelists, ctx.file.suite_module, ctx.file.sdk, ctx.file._target_config] + ctx.files.sources + ctx.files.source_marker, transitive = [ctx.attr._sysroot[DefaultInfo].files] + [t[DefaultInfo].files for t in cmake.tools + ninja.tools]),
        outputs = ctx.outputs.plans,
        mnemonic = "SulongCMakeConfigure",
    )

_common = {
    "sdk": attr.label(allow_single_file = True, mandatory = True, cfg = "exec"),
    "_target_config": attr.label(default = Label("//tools/cc:target_config"), allow_single_file = True),
    "_sysroot": attr.label(default = Label("//tools/cc:all_files")),
    "_runner": attr.label(default = Label("//mx:sulong_cmake"), cfg = "exec", executable = True),
}

_plan = rule(
    implementation = _plan_impl,
    attrs = dict(_common, **{
        "project": attr.string(mandatory = True),
        "variant": attr.string(mandatory = True),
        "cases": attr.string_list(mandatory = True),
        "plans": attr.output_list(mandatory = True),
        "cmakelists": attr.label(allow_single_file = True),
        "suite_module": attr.label(allow_single_file = True),
        "sources": attr.label_list(allow_files = True),
        "source_marker": attr.label(allow_single_file = True),
        "languages": attr.string_list(),
        "config": attr.string_dict(),
    }),
    toolchains = ["@rules_foreign_cc//toolchains:cmake_toolchain", "@rules_foreign_cc//toolchains:ninja_toolchain"],
)

def _case_impl(ctx):
    inputs = ctx.files.srcs + ctx.files.headers + [ctx.file.plan, ctx.file.sdk, ctx.file._target_config]
    args = ctx.actions.args()
    args.add_all(["execute", ctx.file.plan, ctx.outputs.out])
    outputs = [ctx.outputs.out]
    unused = None
    if ctx.files.headers:
        unused = ctx.actions.declare_file(ctx.label.name + ".unused")
        args.add(unused)
        args.add_all(ctx.files.headers)
        outputs.append(unused)
    ctx.actions.run(
        executable = ctx.executable._runner,
        arguments = [args],
        inputs = depset(inputs, transitive = [ctx.attr._sysroot[DefaultInfo].files]),
        outputs = outputs,
        unused_inputs_list = unused,
        mnemonic = "SulongCMakeFixture",
    )

_case = rule(
    implementation = _case_impl,
    attrs = dict(_common, **{
        "plan": attr.label(allow_single_file = True),
        "srcs": attr.label_list(allow_files = True),
        "headers": attr.label_list(allow_files = True),
        "out": attr.output(mandatory = True),
    }),
)

def mx_sulong_cmake_suite(name, root, cases, variants, sdk, modules, languages, config = {}, source_package = None, case_inputs = {}, testonly = True, visibility = None):
    """Configure once per variant. Each installed fixture has its own Bazel action."""
    compatible = ["@platforms//os:linux", "@platforms//cpu:x86_64"]
    prefix = source_package or root + "/"
    srcs = [prefix + case for case in cases]
    companions = [] if source_package else native.glob([root + "/**/*.c", root + "/**/*.cpp"], exclude = srcs, allow_empty = True)
    headers = [source_package + "headers"] if source_package else native.glob([root + "/**/*.h", root + "/**/*.hpp", root + "/**/*.inc"], allow_empty = True)
    recipes = native.glob([modules + "/*"], allow_empty = False) + native.glob([root + "/**/TestOptions.cmake"], allow_empty = True)
    outputs = {case: [] for case in cases}
    for variant in variants:
        filename = variant if variant == "ref.out" else variant + ".bc"
        planner = name + ".cmake/" + variant
        plans = [planner + "/" + case + ".json" for case in cases]
        batch_size = min(64, len(cases)) if source_package else len(cases)
        for start in range(0, len(cases), batch_size):
            batch = cases[start:start + batch_size]
            _plan(
                name = planner + ".part" + str(start) if batch_size < len(cases) else planner,
                project = name,
                variant = variant,
                cases = batch,
                plans = plans[start:start + batch_size],
                sdk = sdk,
                cmakelists = root + "/CMakeLists.txt",
                suite_module = modules + "/SulongTestSuite.cmake",
                sources = [prefix + case for case in batch] + companions + headers + recipes,
                source_marker = source_package + "BUILD.bazel" if source_package else None,
                config = config,
                languages = languages,
                exec_compatible_with = compatible,
                target_compatible_with = compatible,
                testonly = testonly,
            )
        for case, plan in zip(cases, plans):
            target = name + "/" + case
            output = target + ".dir/" + filename
            _case(
                name = target + "." + variant,
                out = output,
                srcs = [prefix + case] + [prefix + extra for extra in case_inputs.get(case, [])] + companions,
                headers = headers,
                plan = plan,
                sdk = sdk,
                exec_compatible_with = compatible,
                target_compatible_with = compatible,
                testonly = testonly,
                visibility = visibility,
            )
            outputs[case].append(":" + output)
    for case, results in outputs.items():
        native.filegroup(name = name + "/" + case, srcs = results, target_compatible_with = compatible, testonly = testonly, visibility = visibility)
    mx_project_files(
        name = name,
        members = {out: out.removeprefix(":" + name + "/") for results in outputs.values() for out in results},
        target_compatible_with = compatible,
        testonly = testonly,
        visibility = visibility,
    )
