"""Build mx CMake/Ninja projects with their declared compiler drivers and inputs."""

load("@rules_foreign_cc//foreign_cc:defs.bzl", "cmake")
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

def mx_cmake_project(name, srcs, compiler, config, include_dirs, profiles, testonly = False, visibility = None):
    """Apply the selected mx CMake configuration, build targets, and installed results."""
    src_group = name + ".cmake_sources"
    native.filegroup(name = src_group, srcs = srcs, testonly = testonly)
    data = [compiler, Label("//tools/cc:target_config"), Label("//tools/cc:all_files")]
    cache = dict(config)
    for key, spec in include_dirs.items():
        inc = name + ".include/" + key
        mx_archive_layout(name = inc, format = "dir", members = {}, archive_imports = {spec[0]: spec[1] + "/*="}, testonly = testonly)
        data.append(":" + inc)
        cache[key] = "$(execpath :" + inc + ")"
    cache.update({
        "CMAKE_C_COMPILER": "$(execpath %s)/bin/cc" % compiler,
        "CMAKE_CXX_COMPILER": "$(execpath %s)/bin/c++" % compiler,
        "CMAKE_AR": "$(execpath %s)/bin/ar" % compiler,
        "CMAKE_RANLIB": "$(execpath %s)/bin/ranlib" % compiler,
        "CMAKE_C_FLAGS": "-Qunused-arguments --config=$(execpath %s)" % Label("//tools/cc:target_config"),
        "CMAKE_CXX_FLAGS": "-Qunused-arguments --config=$(execpath %s)" % Label("//tools/cc:target_config"),
    })
    variants = {}
    modes = {}
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
        cmake(
            name = rule_name,
            lib_name = name + "-" + profile,
            lib_source = ":" + src_group,
            # CMake otherwise reads /etc/debian_version to choose shared library permissions.
            cache_entries = cache | ({} if profile == "macos_arm64" else {"CMAKE_INSTALL_SO_NO_EXE": "ON"}) | recipe["config"],
            build_data = data,
            generate_args = ["-G Ninja"],
            generate_crosstool_file = False,
            exec_compatible_with = [
                "@platforms//os:" + ("macos" if profile == "macos_arm64" else "linux"),
                "@platforms//cpu:" + ("x86_64" if profile == "linux_amd64" else "aarch64"),
            ],
            targets = recipe["targets"],
            install = False,
            postfix_script = "\n".join(['mkdir -p "$$INSTALLDIR/%s" && cp -L "%s" "$$INSTALLDIR/%s"' % (m.rpartition("/")[0] or ".", m, m) for m in recipe["members"]]),
            out_include_dir = "",
            out_data_files = recipe["members"],
            testonly = testonly,
            target_compatible_with = select({condition: [], "//conditions:default": ["@platforms//:incompatible"]}),
        )
        variants[condition] = members
        modes[condition] = install_modes
    mx_project_files(
        name = name,
        members = select(variants | {"//conditions:default": {}}),
        modes = select(modes | {"//conditions:default": {}}),
        target_compatible_with = select({c: [] for c in variants} | {"//conditions:default": ["@platforms//:incompatible"]}),
        testonly = testonly,
        visibility = visibility,
    )
