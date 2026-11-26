"""Generate mx Native Image executables with the registered rules_graalvm toolchain."""

load("@bazel_skylib//lib:shell.bzl", "shell")
load("@rules_graalvm//graalvm:defs.bzl", "native_image")
load("//graal:layout.bzl", "MxArchiveLayoutInfo", "mx_archive_layout")

def _search_path(labels):
    paths = ["$(execpath " + label + ")" for label in labels]
    return select({"@platforms//os:windows": [";".join(paths)], "//conditions:default": [":".join(paths)]})

def _mx_image(name, binary, image_args, classpath, modulepath, **kwargs):
    """Use the runtime artifacts and search order resolved by mx for this image."""
    args = []
    if classpath:
        args += ["-cp"] + _search_path(classpath)
    if modulepath:
        args += ["--module-path"] + _search_path(modulepath)
    native_image(
        name = name,
        executable_name = binary,
        data = classpath + modulepath,
        deps = [],
        extra_args = args + image_args,
        **kwargs
    )

def mx_native_image_executable(name, main_class, output, image_args, classpath = [], modulepath = [], visibility = None):
    """Build a standalone executable from an mx image project."""
    binary = select({"@platforms//os:windows": output + ".exe", "//conditions:default": output})
    _mx_image(name, binary, image_args, classpath, modulepath, main_class = main_class, visibility = visibility)

def mx_native_image_library(name, output, image_args, classpath = [], modulepath = [], visibility = None):
    """Build a Truffle language shared library with its JNI invocation API."""
    binary = select({"@platforms//os:macos": "lib" + output + ".dylib", "//conditions:default": "lib" + output + ".so"})
    _mx_image(
        name,
        binary,
        image_args,
        classpath,
        modulepath,
        shared_library = True,
        visibility = visibility,
        target_compatible_with = select({
            "@platforms//os:linux": [],
            "@platforms//os:macos": [],
            "//conditions:default": ["@platforms//:incompatible"],
        }),
    )

def _launcher_script_impl(ctx):
    entries = {}
    support = "." + ctx.attr.output
    args = []
    for flag, files in [("-cp", ctx.files.classpath), ("-p", ctx.files.modulepath)]:
        paths = []
        for i, jar in enumerate(files):
            path = "%s/%s/%d/%s" % (support, flag[1:], i, jar.basename)
            entries[path] = jar
            paths.append('"$dir"/' + shell.quote(path))
        if paths:
            args.extend([flag, ":".join(paths)])
    for arg in ctx.attr.java_args:
        if arg == "-Dorg.graalvm.launcher.executablePath={argv0}":
            args.append('"-Dorg.graalvm.launcher.executablePath=$0"')
        else:
            args.append(shell.quote(arg))
    script = ctx.actions.declare_file(ctx.label.name + "/" + ctx.attr.output)
    ctx.actions.write(script, """#!/bin/sh
case "$0" in
    */*) dir=${0%%/*} ;;
    *) dir=. ;;
esac
if [ "${VERBOSE_GRAALVM_LAUNCHERS:-}" = true ]; then set -x; fi
exec "$dir"/%s %s "$@"
""" % (shell.quote(support + "/jdk/bin/java"), " ".join(args)), is_executable = True)
    entries[ctx.attr.output] = script
    return [DefaultInfo(files = depset(entries.values())), MxArchiveLayoutInfo(archive = None, entries = entries)]

_launcher_script = rule(
    implementation = _launcher_script_impl,
    attrs = {
        "output": attr.string(mandatory = True),
        "java_args": attr.string_list(mandatory = True),
        "classpath": attr.label_list(allow_files = [".jar"]),
        "modulepath": attr.label_list(allow_files = [".jar"]),
    },
)

def mx_jvm_tool_launcher(name, output, java_args, jdk_archive, classpath = [], modulepath = [], visibility = None):
    """Install the Java tool and its ordered search paths relative to its entry point."""
    _launcher_script(name = name + ".script", output = output, java_args = java_args, classpath = classpath, modulepath = modulepath)
    mx_archive_layout(
        name = name,
        format = "dir",
        members = {},
        archive_imports = {":" + name + ".script": "="},
        jdk_homes = {jdk_archive: json.encode({"directory": "." + output + "/jdk", "exclude": []})},
        visibility = visibility,
    )

def _tool_layout_impl(ctx):
    layout = ctx.attr.tool[MxArchiveLayoutInfo] if ctx.attr.launcher else None
    tool = layout.entries[ctx.attr.launcher] if layout else ctx.file.tool
    root = ctx.label.name + ".files/"
    entries = dict(layout.entries) if layout else {}
    links = {}
    executable = ctx.actions.declare_file(root + tool.basename)
    ctx.actions.symlink(output = executable, target_file = tool, is_executable = True)
    entries[tool.basename] = executable
    for name, dest in ctx.attr.links.items():
        if ctx.target_platform_has_constraint(ctx.attr._windows[platform_common.ConstraintValueInfo]):
            file = ctx.actions.declare_file(root + name + ".cmd")
            ctx.actions.write(output = file, content = ctx.attr.windows_command)
        else:
            file = ctx.actions.declare_symlink(root + name)
            ctx.actions.symlink(output = file, target_path = dest)
            links[name] = dest
        entries[file.basename] = file
    return [
        DefaultInfo(files = depset(entries.values()), runfiles = ctx.runfiles(files = entries.values())),
        MxArchiveLayoutInfo(archive = None, entries = entries, links = links, jdk_homes = layout.jdk_homes if layout else []),
    ]

_tool_layout = rule(
    implementation = _tool_layout_impl,
    attrs = {
        "launcher": attr.string(),
        "tool": attr.label(mandatory = True, allow_single_file = True),
        "links": attr.string_dict(mandatory = True),
        "windows_command": attr.string(mandatory = True),
        "_windows": attr.label(default = "@platforms//os:windows"),
    },
)

def mx_toolchain_tool_layout(name, tool, links, windows_command, launcher = None, visibility = None):
    """Expose the upstream tool aliases and packaged runtime dependencies."""
    _tool_layout(
        name = name + ".aliases" if launcher else name,
        tool = tool,
        links = links,
        windows_command = windows_command,
        launcher = launcher,
        visibility = visibility,
    )
    if launcher:
        mx_archive_layout(
            name = name,
            format = "dir",
            members = {},
            archive_imports = {":" + name + ".aliases": "="},
            visibility = visibility,
        )

def _bootstrap_scripts_impl(ctx):
    entries = {}
    flags = []
    for flag, jars in [("-cp", ctx.files.classpath), ("-p", ctx.files.modulepath)]:
        paths = []
        for i, jar in enumerate(jars):
            path = ".bootstrap/%s/%d/%s" % (flag[1:], i, jar.basename)
            entries[path] = jar
            paths.append('"$root"/' + shell.quote(path))
        if paths:
            flags.extend([flag, ":".join(paths)])
    flags.extend([shell.quote(arg) for arg in ctx.attr.java_args])
    flags.extend(['"-Dllvm.bin.dir=$root/.bootstrap/llvm/bin"', '"-Dorg.graalvm.language.llvm.home=$root/.bootstrap/' + ctx.attr.home + '"', "-Xlog:disable"])
    for alias, main in ctx.attr.aliases.items():
        script = ctx.actions.declare_file(ctx.label.name + "/bin/" + alias)
        content = """#!/bin/sh
root=${0%%/*}/..
exec "$root/.bootstrap/jdk/bin/java" %s %s %s "$@"
""" % (" ".join(flags), shell.quote("-Dorg.graalvm.launcher.executablename=" + alias), shell.quote(main))
        ctx.actions.write(script, content, is_executable = True)
        entries["bin/" + alias] = script
    ninja = ctx.actions.declare_file(ctx.label.name + "/toolchain.ninja")
    ctx.actions.write(ninja, ctx.attr.ninja)
    entries["toolchain.ninja"] = ninja
    return [DefaultInfo(files = depset(entries.values())), MxArchiveLayoutInfo(archive = None, entries = entries)]

_bootstrap_scripts = rule(
    implementation = _bootstrap_scripts_impl,
    attrs = {
        "aliases": attr.string_dict(),
        "java_args": attr.string_list(),
        "classpath": attr.label_list(allow_files = [".jar"]),
        "modulepath": attr.label_list(allow_files = [".jar"]),
        "ninja": attr.string(),
        "home": attr.string(),
    },
)

def mx_bootstrap_launchers(name, aliases, ninja, sdk, jdk_archive, java_args = [], classpath = [], modulepath = [], home = None, **kwargs):
    """Package the Java compiler drivers with a relocatable LLVM SDK and JDK."""
    _bootstrap_scripts(name = name + ".scripts", aliases = aliases, ninja = ninja, classpath = classpath, modulepath = modulepath, java_args = java_args, home = "home" if home else "nonexistent")
    mx_archive_layout(
        name = name,
        format = "dir",
        members = {},
        archive_imports = {":" + name + ".scripts": "="} | ({home: "=.bootstrap/home/"} if home else {}),
        jdk_homes = {jdk_archive: json.encode({"directory": ".bootstrap/jdk", "exclude": []})},
        tar_trees = {sdk: ".bootstrap/llvm"},
        **kwargs
    )
