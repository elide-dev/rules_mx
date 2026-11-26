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

MxArchiveLayoutInfo = provider(
    doc = "Mapping of packaged archive paths to their declared Bazel files.",
    fields = ["archive", "entries", "links", "jdk_homes"],
)

def _add_entry(entries, path, file):
    if path in entries:
        fail("Duplicate archive member: " + path)
    entries[path] = file

def _layout_impl(ctx):
    directory = ctx.attr.format == "dir"
    output = ctx.actions.declare_directory(ctx.label.name + ".files") if directory else ctx.actions.declare_file(ctx.label.name.lower().replace("_", "-") + "." + ctx.attr.format)
    entries = {}
    links = {}
    homes = []
    for target, encoded in ctx.attr.jdk_homes.items():
        [bundle] = target.files.to_list()
        spec = json.decode(encoded)
        homes.append(struct(bundle = bundle, directory = spec["directory"], exclude = spec["exclude"]))
    for target, destinations in ctx.attr.members.items():
        files = [target[MxArchiveLayoutInfo].archive] if MxArchiveLayoutInfo in target and target[MxArchiveLayoutInfo].archive else target.files.to_list()
        if len(files) != 1:
            fail("Expected one file from " + str(target.label))
        source = files[0]
        for destination in destinations.split("\n"):
            path = destination.replace("{basename}", source.basename).replace("{target}", ctx.attr.target_prefix)
            _add_entry(entries, path, source)
    for target, imports in ctx.attr.archive_imports.items():
        sources = target[MxArchiveLayoutInfo].entries
        source_links = getattr(target[MxArchiveLayoutInfo], "links", {})
        for spec in imports.split("\n"):
            source, separator, destination = spec.partition("=")
            if not separator:
                fail("Expected an archive member mapping: " + spec)
            if source:
                if source not in sources:
                    fail("No archive member " + source + " in " + str(target.label))
                _add_entry(entries, destination, sources[source])
                if source in source_links:
                    links[destination] = source_links[source]
            else:
                if destination and not destination.endswith("/"):
                    fail("Expected a destination directory: " + destination)
                for home in getattr(target[MxArchiveLayoutInfo], "jdk_homes", []):
                    homes.append(struct(bundle = home.bundle, directory = destination + home.directory, exclude = home.exclude))
                for path, artifact in sources.items():
                    _add_entry(entries, destination + path, artifact)
                    if path in source_links:
                        links[destination + path] = source_links[path]
    args = ctx.actions.args()
    args.add(output.path)
    if directory:
        args.add("--directory")
    if links:
        args.add("--links")
        args.add(json.encode(links))
    if homes:
        args.add("--jdk-homes")
        args.add(json.encode([{"bundle": h.bundle.path, "directory": h.directory, "exclude": h.exclude} for h in homes]))
    for path in sorted(entries):
        args.add(path)
        args.add(entries[path])
    ctx.actions.run(
        executable = ctx.executable._pack,
        arguments = [args],
        inputs = entries.values() + [h.bundle for h in homes],
        outputs = [output],
        mnemonic = "MxArchiveLayout",
    )
    files = [output]
    if ctx.attr.file_list:
        if sorted(ctx.attr.file_list) != sorted(entries):
            fail("mx file list differs from archive members for " + str(ctx.label))
        file_list = ctx.actions.declare_file(output.basename + ".filelist")
        ctx.actions.write(file_list, "\n".join(ctx.attr.file_list))
        files.append(file_list)
    return [
        DefaultInfo(files = depset(files)),
        MxArchiveLayoutInfo(archive = None if directory else output, entries = entries, links = links, jdk_homes = homes),
    ]

mx_archive_layout = rule(
    implementation = _layout_impl,
    attrs = {
        # One input can be copied to multiple archive paths.
        "members": attr.label_keyed_string_dict(allow_files = True, mandatory = True),
        # Map a member to a new path, or use an empty selector to import an entire layout.
        "archive_imports": attr.label_keyed_string_dict(providers = [MxArchiveLayoutInfo]),
        "jdk_homes": attr.label_keyed_string_dict(allow_files = [".gz"]),
        "file_list": attr.string_list(),
        "format": attr.string(mandatory = True, values = ["dir", "jar", "tar", "tar.gz"]),
        "target_prefix": attr.string(),
        "_pack": attr.label(default = Label("//mx:pack_layout"), executable = True, cfg = "exec"),
    },
)
