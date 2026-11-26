"""Cross-check the three Sulong -Os fixtures against an independent upstream CMake build."""

load("@rules_foreign_cc//foreign_cc:defs.bzl", "cmake")
load("@rules_python//python:py_test.bzl", "py_test")

def sulong_fixture_check(name, project, root, modules, sdk):
    compatible = ["@platforms//os:linux", "@platforms//cpu:x86_64"]
    cases = [src.removeprefix(root + "/") for src in native.glob([root + "/Os/*.c"])]
    srcs = name + "_srcs"
    native.filegroup(name = srcs, srcs = [root + "/CMakeLists.txt"] + [root + "/" + c for c in cases])
    golden = name + "_upstream"
    driver = str(Label("@elide_toolchain_linux_amd64//:cc"))
    module_dir = "$$EXT_BUILD_ROOT$$/" + native.package_relative_label(":" + srcs).workspace_root + "/" + native.package_name() + "/" + modules
    cmake(
        name = golden,
        lib_source = ":" + srcs,
        generate_args = ["-G Ninja"],
        build_data = [sdk, driver] + native.glob([modules + "/*"]),
        cache_entries = {
            "CLANG": "$(execpath %s)" % driver,
            "CLANGXX": "$(execpath %s)" % driver,
            "LLVM_CONFIG": "$(execpath %s)/bin/llvm-config" % sdk,
            "CMAKE_MODULE_PATH": module_dir,
            "SULONG_MODULE_PATH": module_dir,
            "SULONG_PROJECT_NAME": project,
            "SULONG_CURRENT_VARIANT": "ref.out",
            "SULONG_BUILD_SHARED_OBJECT": "NO",
            "SULONG_ENABLED_LANGUAGES": "C",
            "SULONG_TESTS": ";".join(cases),
        },
        configuration = "Sulong",
        out_include_dir = "",
        out_data_files = [c + ".dir/ref.out" for c in cases],
        exec_compatible_with = compatible,
        target_compatible_with = compatible,
        testonly = True,
    )
    src = Label("//graal:sulong_fixture_test.py")
    variants = [project + "/" + c + "." + v for c in cases for v in ["ref.out", "bitcode-Os"]]
    py_test(
        name = name,
        srcs = [src],
        main = src,
        size = "small",
        env = {"UPSTREAM_OUTPUTS": "$(rootpaths :%s)" % golden},
        args = ["$(rootpath %s)" % sdk] + ["$(rootpath :%s)" % v for v in variants],
        data = [sdk, ":" + golden] + variants,
        exec_compatible_with = compatible,
        target_compatible_with = compatible,
        visibility = ["//visibility:public"],
    )
