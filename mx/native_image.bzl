"""Generate mx Native Image executables with the registered rules_graalvm toolchain."""

load("@rules_graalvm//graalvm:defs.bzl", "native_image")
load("//graal:layout.bzl", "MxArchiveLayoutInfo")

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

def _tool_layout_impl(ctx):
    tool = ctx.file.tool
    root = ctx.label.name + ".files/"
    entries = {}
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
        MxArchiveLayoutInfo(archive = None, entries = entries, links = links),
    ]

mx_toolchain_tool_layout = rule(
    implementation = _tool_layout_impl,
    attrs = {
        "tool": attr.label(mandatory = True, allow_single_file = True),
        "links": attr.string_dict(mandatory = True),
        "windows_command": attr.string(mandatory = True),
        "_windows": attr.label(default = "@platforms//os:windows"),
    },
)
