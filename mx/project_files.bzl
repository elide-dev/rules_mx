"""Expose the archive names that mx assigns to source and compiled project results."""

load("//graal:layout.bzl", "MxArchiveLayoutInfo")

def _impl(ctx):
    entries = {}
    for target, path in ctx.attr.members.items():
        if path in entries:
            fail("Duplicate project result " + path)
        (source,) = target.files.to_list()
        entries[path] = source
    modes = dict(ctx.attr.modes)
    if ctx.attr.primary:
        modes[ctx.attr.primary] = "755"
    return [
        DefaultInfo(files = depset(entries.values() + ctx.files.srcs)),
        MxArchiveLayoutInfo(archive = None, entries = entries, primary = ctx.attr.primary, modes = modes),
    ]

mx_project_files = rule(
    implementation = _impl,
    attrs = {
        "primary": attr.string(),
        "modes": attr.string_dict(),
        "members": attr.label_keyed_string_dict(allow_files = True),
        "srcs": attr.label_list(allow_files = True),
    },
)

def _compile(ctx):
    ctx.actions.run(
        executable = ctx.executable.tool,
        arguments = [ctx.file.src.path, "-o", ctx.outputs.out.path] + ctx.attr.flags,
        inputs = [ctx.file.src],
        outputs = [ctx.outputs.out],
        mnemonic = "WabtTranslate",
    )

wabt_translate = rule(
    implementation = _compile,
    attrs = {
        "tool": attr.label(executable = True, cfg = "exec", mandatory = True),
        "flags": attr.string_list(),
        "src": attr.label(allow_single_file = [".wat", ".wasm"], mandatory = True),
        "out": attr.output(mandatory = True),
    },
)

def mx_wat_project(name, root, cases, tool, testonly, visibility = None):
    """Compile and expose each upstream WAT program and its binary."""
    members = {}
    for case in cases:
        src = root + "/" + case
        wasm = case.removesuffix(".wat") + ".wasm"
        target = name + "/" + wasm
        wabt_translate(name = target, flags = ["--enable-exceptions"], src = src, out = name + ".files/" + wasm, tool = tool, testonly = testonly, visibility = visibility)
        members[":" + target] = wasm
        members[src] = case
    mx_project_files(name = name, members = members, testonly = testonly, visibility = visibility)

def _source_archive_impl(ctx):
    output = ctx.actions.declare_file(ctx.label.name + ".zip")
    files = {}
    for group, directory in ctx.attr.groups.items():
        for src in group.files.to_list():
            member = src.basename if directory == "." else directory + "/" + src.basename
            if member in files:
                fail("Duplicate source archive member " + member)
            files[member] = src
    args = ctx.actions.args()
    args.add(output)
    for member in sorted(files):
        args.add(member)
        args.add(files[member])
    ctx.actions.run(executable = ctx.executable._pack, arguments = [args], inputs = files.values(), outputs = [output], mnemonic = "MxSourceArchive")
    return [DefaultInfo(files = depset([output]))]

mx_source_archive = rule(
    implementation = _source_archive_impl,
    attrs = {
        "groups": attr.label_keyed_string_dict(mandatory = True),
        "_pack": attr.label(default = Label("//mx:pack_layout"), executable = True, cfg = "exec"),
    },
)

def _platform_file_impl(ctx):
    out = ctx.actions.declare_file(ctx.label.name + "/" + ctx.attr.filename)
    ctx.actions.write(out, ctx.attr.content)
    return [
        DefaultInfo(files = depset([out])),
        MxArchiveLayoutInfo(archive = None, entries = {out.basename: out}, modes = {out.basename: "644"}),
    ]

_generated_file = rule(
    implementation = _platform_file_impl,
    attrs = {"filename": attr.string(mandatory = True), "content": attr.string(mandatory = True)},
)

def mx_platform_file(name, filename, variants, **kwargs):
    """Materialize the content supplied by the mx project for the target OS and libc."""
    _generated_file(
        name = name,
        filename = filename,
        content = select({Label("//tools/cc:linux_musl") if os == "linux-musl" else "@platforms//os:" + os: content for os, content in variants.items()}),
        **kwargs
    )

def _release_file_impl(ctx):
    out = ctx.actions.declare_file(ctx.label.name + "/" + ctx.attr.filename)
    args = ctx.actions.args()
    args.add_all([out, ctx.file.parent_release, ctx.file.provenance, ctx.attr.graalvm_version])
    args.add_all(ctx.attr.suites)
    ctx.actions.run(
        executable = ctx.executable._writer,
        arguments = [args],
        inputs = [ctx.file.parent_release, ctx.file.provenance],
        outputs = [out],
        mnemonic = "MxReleaseFile",
    )
    return [
        DefaultInfo(files = depset([out])),
        MxArchiveLayoutInfo(archive = None, entries = {out.basename: out}, modes = {out.basename: "644"}),
    ]

mx_release_file = rule(
    implementation = _release_file_impl,
    attrs = {
        "filename": attr.string(mandatory = True),
        "parent_release": attr.label(allow_single_file = True, mandatory = True),
        "provenance": attr.label(allow_single_file = True, mandatory = True),
        "graalvm_version": attr.string(mandatory = True),
        "suites": attr.string_list(mandatory = True),
        "_writer": attr.label(default = Label("//mx:release_file"), executable = True, cfg = "exec"),
    },
)
