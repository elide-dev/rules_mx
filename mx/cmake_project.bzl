"""Build mx CMake/Ninja projects with their declared compiler drivers and inputs."""

load("@rules_foreign_cc//foreign_cc:defs.bzl", "cmake")
load("@rules_python//python:py_binary.bzl", "py_binary")
load("//graal:layout.bzl", "mx_archive_layout")
load("//mx:project_files.bzl", "mx_project_files")

def _installed_impl(ctx):
    suffix = "/" + ctx.attr.member
    files = [f for f in ctx.attr.project.files.to_list() if f.path.endswith(suffix)]
    if len(files) != 1:
        fail("Expected one installed CMake output " + ctx.attr.member)
    return [DefaultInfo(files = depset(files))]

_installed = rule(
    implementation = _installed_impl,
    attrs = {"project": attr.label(mandatory = True), "member": attr.string(mandatory = True)},
)

def mx_cmake_project(name, srcs, compiler, config, include_dirs, profiles, testonly = False, visibility = None, source_subdir = "", wrapper = None, max_jobs = 0):
    """Apply the selected mx CMake configuration, build targets, and installed results."""
    src_group = name + ".cmake_sources"
    native.filegroup(name = src_group, srcs = srcs, testonly = testonly)
    data = [compiler, Label("//tools/cc:target_config"), Label("//tools/cc:all_files")]
    if wrapper:
        py_binary(name = name + ".compiler_wrapper", main = wrapper, srcs = [wrapper], testonly = testonly)
        data.append(":" + name + ".compiler_wrapper")
    cache = dict(config)
    for key, spec in include_dirs.items():
        inc = name + ".include/" + key
        mx_archive_layout(name = inc, format = "dir", members = {}, archive_imports = {spec[0]: spec[1] + "/*="}, testonly = testonly)
        data.append(":" + inc)
        cache[key] = "$(execpath :" + inc + ")"
    config_file = "$(execpath %s)" % Label("//tools/cc:target_config")
    cc_flags = "-Qunused-arguments --config="
    cache.update({
        "CMAKE_C_COMPILER": "$(execpath %s)/bin/cc" % compiler,
        "CMAKE_CXX_COMPILER": "$(execpath %s)/bin/c++" % compiler,
        "CMAKE_AR": "$(execpath %s)/bin/ar" % compiler,
        "CMAKE_RANLIB": "$(execpath %s)/bin/ranlib" % compiler,
        "CMAKE_C_FLAGS": cc_flags + config_file,
        "CMAKE_CXX_FLAGS": cc_flags + config_file,
    })
    variants = {}
    modes = {}
    installed = {}
    for profile, recipe in profiles.items():
        condition = Label("//tools/cc:profile_" + profile)
        rule_name = name + ".cmake/" + profile
        members = {}
        install_modes = {}
        for member in recipe["members"]:
            label = rule_name + "/" + member
            _installed(name = label, project = ":" + rule_name, member = member, testonly = testonly)
            members[":" + label] = member
            if (member.endswith(".so") or member.endswith(".dylib")) or ".so." in member.rpartition("/")[2]:
                install_modes[member] = "755" if profile == "macos_arm64" else "644"

        # Native CMake otherwise asks xcrun for the executor's default SDK.
        if profile == "macos_arm64":
            sdk = Label("@elide_toolchain_darwin_arm64//:all_files")
            sysroot = "$$EXT_BUILD_ROOT$$/" + sdk.workspace_root + "/apple-sdk"
            platform_config = {"CMAKE_OSX_SYSROOT": sysroot}

            # The Sulong driver delegates to xcrun. CMake's compiler probes may
            # omit CMAKE_OSX_SYSROOT until they have identified the compiler.
            for key in ("CMAKE_C_FLAGS", "CMAKE_CXX_FLAGS"):
                platform_config[key] = "-isysroot " + sysroot + " " + cc_flags + "$$EXT_BUILD_ROOT$$/" + config_file
        else:
            # CMake otherwise reads /etc/debian_version to choose shared library permissions.
            platform_config = {"CMAKE_INSTALL_SO_NO_EXE": "ON"}
        directories = recipe.get("directories", [])
        platform_config = cache | platform_config
        for key, value in recipe["config"].items():
            if value == "@SOURCE_PREFIX_MAP@":
                source = "$$EXT_BUILD_ROOT$$/" + native.package_relative_label(srcs[0]).workspace_root + "/src"
                flags = "-ffile-prefix-map=" + source + "=llvm-project/src -ffile-prefix-map=$$BUILD_TMPDIR$$=llvm-project"
                platform_config[key] = platform_config.get(key, "") + " " + flags
            else:
                platform_config[key] = value.replace("@COMPILER@", "$(execpath %s)" % compiler).replace("@WRAPPER@", "$(execpath :%s.compiler_wrapper)" % name)
        if directories:
            directory = directories[0]
            _installed(name = rule_name + ".installed", project = ":" + rule_name, member = directory, testonly = testonly)
            installed[condition] = ":" + rule_name + ".installed"
            platform_config["Python3_EXECUTABLE"] = "$$EXT_BUILD_ROOT$$/$(PYTHON3)"

            # rules_foreign_cc owns CMAKE_INSTALL_PREFIX. Preserve the directory
            # name under which mx publishes that complete installation.
            stage = 'mv "$$INSTALLDIR" "$$BUILD_TMPDIR/mx-install" && mkdir -p "$$INSTALLDIR" && mv "$$BUILD_TMPDIR/mx-install" "$$INSTALLDIR/%s"' % directories[0]
        else:
            stage = "\n".join(['mkdir -p "$$INSTALLDIR/%s" && cp -L "%s" "$$INSTALLDIR/%s"' % (m.rpartition("/")[0] or ".", m, m) for m in recipe["members"]])
        cmake(
            name = rule_name,
            lib_name = name + "-" + profile,
            lib_source = ":" + src_group,
            cache_entries = platform_config,
            working_directory = "src/" + source_subdir if source_subdir else "",
            build_data = data,
            toolchains = [Label("@rules_python//python:current_py_toolchain")] if source_subdir else [],
            env = {"CMAKE_BUILD_PARALLEL_LEVEL": str(max_jobs)} if max_jobs else {},
            generate_args = ["-G Ninja"],
            generate_crosstool_file = False,
            exec_compatible_with = [
                "@platforms//os:" + ("macos" if profile == "macos_arm64" else "linux"),
                "@platforms//cpu:" + ("x86_64" if profile == "linux_amd64" else "aarch64"),
            ],
            targets = recipe["targets"],
            install = False,
            postfix_script = stage,
            out_include_dir = "",
            out_data_files = recipe["members"],
            out_data_dirs = directories,
            testonly = testonly,
            target_compatible_with = select({condition: [], "//conditions:default": ["@platforms//:incompatible"]}),
        )
        variants[condition] = members
        modes[condition] = install_modes
    if installed:
        native.alias(name = name, actual = select(installed), visibility = visibility, testonly = testonly)
        return
    mx_project_files(
        name = name,
        members = select(variants | {"//conditions:default": {}}),
        modes = select(modes | {"//conditions:default": {}}),
        target_compatible_with = select({c: [] for c in variants} | {"//conditions:default": ["@platforms//:incompatible"]}),
        testonly = testonly,
        visibility = visibility,
    )
