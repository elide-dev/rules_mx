"""Archive explicit layout inputs under their mx distribution paths."""

load("@rules_cc//cc/common:cc_info.bzl", "CcInfo")

def _static_archive_impl(ctx):
    libraries = [
        library
        for linker in ctx.attr.library[CcInfo].linking_context.linker_inputs.to_list()
        if linker.owner == ctx.attr.library.label
        for library in linker.libraries
    ]
    if len(libraries) != 1:
        fail("Expected one static library from " + str(ctx.attr.library.label))
    archive = libraries[0].pic_static_library or libraries[0].static_library
    if not archive:
        fail("No static archive from " + str(ctx.attr.library.label))
    return [DefaultInfo(files = depset([archive]))]

static_archive = rule(
    implementation = _static_archive_impl,
    attrs = {"library": attr.label(mandatory = True, providers = [CcInfo])},
)

def _layout_impl(ctx):
    output = ctx.actions.declare_file(ctx.label.name.lower().replace("_", "-") + "." + ctx.attr.format)
    entries = {}
    for target, destinations in ctx.attr.members.items():
        files = target.files.to_list()
        if len(files) != 1:
            fail("Expected one file from " + str(target.label))
        source = files[0]
        for destination in destinations.split("\n"):
            path = destination.replace("{basename}", source.basename).replace("{target}", ctx.attr.target_prefix)
            if path in entries:
                fail("Duplicate archive member: " + path)
            entries[path] = source
    args = ctx.actions.args()
    args.add(output)
    for path in sorted(entries):
        args.add(path)
        args.add(entries[path])
    ctx.actions.run(
        executable = ctx.executable._pack,
        arguments = [args],
        inputs = entries.values(),
        outputs = [output],
        mnemonic = "MxArchiveLayout",
    )
    return [DefaultInfo(files = depset([output]))]

mx_archive_layout = rule(
    implementation = _layout_impl,
    attrs = {
        # One input can be copied to multiple archive paths.
        "members": attr.label_keyed_string_dict(allow_files = True, mandatory = True),
        "format": attr.string(mandatory = True, values = ["jar", "tar"]),
        "target_prefix": attr.string(),
        "_pack": attr.label(default = Label("//mx:pack_layout"), executable = True, cfg = "exec"),
    },
)
