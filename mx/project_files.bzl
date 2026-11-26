"""Expose the archive names that mx assigns to source and compiled project results."""

load("//graal:layout.bzl", "MxArchiveLayoutInfo")

def _impl(ctx):
    entries = {}
    for target, path in ctx.attr.members.items():
        if path in entries:
            fail("Duplicate project result " + path)
        (source,) = target.files.to_list()
        entries[path] = source
    return [
        DefaultInfo(files = depset(entries.values() + ctx.files.srcs)),
        MxArchiveLayoutInfo(archive = None, entries = entries, primary = ctx.attr.primary, modes = {ctx.attr.primary: "755"} if ctx.attr.primary else {}),
    ]

mx_project_files = rule(
    implementation = _impl,
    attrs = {
        "primary": attr.string(),
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
