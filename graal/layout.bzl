"""Archive explicit layout inputs under their mx distribution paths."""

load("@rules_cc//cc/common:cc_info.bzl", "CcInfo")
load("@rules_java//java/common:java_info.bzl", "JavaInfo")
load(":layout_info.bzl", _MxArchiveLayoutInfo = "MxArchiveLayoutInfo")

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

MxArchiveLayoutInfo = _MxArchiveLayoutInfo

def _add_entry(entries, path, file):
    if path in entries:
        fail("Duplicate archive member: " + path)
    entries[path] = file

def _layout_args(ctx):
    args = ctx.actions.args()
    if ctx.attr.use_param_file:
        args.use_param_file("--args-file=%s", use_always = True)
        args.set_param_file_format("multiline")
    return args

def _layout_impl(ctx):
    directory = ctx.attr.format == "dir"
    release_prefix = ctx.attr.release_prefix
    if release_prefix:
        output = ctx.actions.declare_file(release_prefix + ".tar.gz")
    else:
        output = ctx.actions.declare_directory(ctx.label.name + ".files") if directory else ctx.actions.declare_file(ctx.label.name.lower().replace("_", "-") + "." + ctx.attr.format)
    entries = {}
    modes = {}
    links = {}
    homes = []
    image_groups = []
    for path, value in ctx.attr.literals.items():
        file = ctx.actions.declare_file(ctx.label.name + ".literals/" + path)
        ctx.actions.write(file, value)
        _add_entry(entries, path, file)
        modes[path] = "644"
    tar_trees = [(target.files.to_list()[0], dest) for target, dest in ctx.attr.tar_trees.items()]
    for target, encoded in ctx.attr.jdk_homes.items():
        [bundle] = target.files.to_list()
        spec = json.decode(encoded)
        for home in spec if type(spec) == "list" else [spec]:
            homes.append(struct(bundle = bundle, directory = home["directory"], exclude = home["exclude"]))
    for target, destinations in ctx.attr.members.items():
        files = [target[MxArchiveLayoutInfo].archive] if MxArchiveLayoutInfo in target and target[MxArchiveLayoutInfo].archive else target.files.to_list()
        if len(files) != 1:
            fail("Expected one file from " + str(target.label))
        source = files[0]
        for destination in destinations.split("\n"):
            path = destination.replace("{basename}", source.basename).replace("{target}", ctx.attr.target_prefix)
            _add_entry(entries, path, source)
            if source.extension in ("jar", "zip"):
                modes[path] = "644"
    for target, imports in ctx.attr.archive_imports.items():
        sources = target[MxArchiveLayoutInfo].entries
        source_links = getattr(target[MxArchiveLayoutInfo], "links", {})
        source_modes = getattr(target[MxArchiveLayoutInfo], "modes", {})
        for spec in imports.split("\n"):
            source, separator, destination = spec.partition("=")
            if not separator:
                fail("Expected an archive member mapping: " + spec)
            if source:
                if source.endswith("/*"):
                    prefix = source[:-1]
                    matches = [path for path in sources if path.startswith(prefix)]
                    groups = [(image, kinds, destination + mount[len(prefix):]) for image, kinds, mount in getattr(target[MxArchiveLayoutInfo], "image_groups", []) if mount.startswith(prefix)]
                    image_groups.extend(groups)
                    if not matches and not groups:
                        fail("No archive members under " + prefix + " in " + str(target.label))
                    for path in matches:
                        member = destination + path[len(prefix):]
                        _add_entry(entries, member, sources[path])
                        if path in source_modes:
                            modes[member] = source_modes[path]
                        if path in source_links:
                            links[member] = source_links[path]
                    continue
                if source == "{primary}":
                    source = target[MxArchiveLayoutInfo].primary
                    destination = destination.replace("{basename}", source.rpartition("/")[2])
                if source.startswith("*/"):
                    # mx multitarget results start with the selected os-architecture pair.
                    matches = [p for p in sources if p.partition("/")[2] == source[2:]]
                    if len(matches) != 1:
                        fail("Expected one member matching %s in %s, got %s" % (source, target.label, matches))
                    source = matches[0]
                if source not in sources:
                    fail("No archive member " + source + " in " + str(target.label))
                _add_entry(entries, destination, sources[source])
                if source in source_modes:
                    modes[destination] = source_modes[source]
                if source in source_links:
                    links[destination] = source_links[source]
            else:
                if destination and not destination.endswith("/"):
                    fail("Expected a destination directory: " + destination)
                for image, kinds, mount in getattr(target[MxArchiveLayoutInfo], "image_groups", []):
                    image_groups.append((image, kinds, destination + mount))
                for bundle, mount in getattr(target[MxArchiveLayoutInfo], "tar_trees", []):
                    tar_trees.append((bundle, destination + mount))
                for home in getattr(target[MxArchiveLayoutInfo], "jdk_homes", []):
                    homes.append(struct(bundle = home.bundle, directory = destination + home.directory, exclude = home.exclude))
                for path, artifact in sources.items():
                    _add_entry(entries, destination + path, artifact)
                    if path in source_modes:
                        modes[destination + path] = source_modes[path]
                    if path in source_links:
                        links[destination + path] = source_links[path]
    resource_jar = None
    if ctx.attr.file_list_entry:
        if links or homes or tar_trees or image_groups or not directory or not ctx.attr.hash_entry:
            fail("Resource metadata requires a directory of regular files")
        manifest = ctx.actions.declare_file(ctx.label.name + ".metadata/files")
        digest = ctx.actions.declare_file(ctx.label.name + ".metadata/sha256")
        args = _layout_args(ctx)
        args.add_all(["--metadata", manifest, digest])
        for path in sorted(entries):
            args.add(path)
            args.add(entries[path])
            args.add(modes.get(path, "source"))
        ctx.actions.run(executable = ctx.executable._pack, arguments = [args], inputs = entries.values(), outputs = [manifest, digest], mnemonic = "MxResourceMetadata")
        _add_entry(entries, ctx.attr.file_list_entry, manifest)
        _add_entry(entries, ctx.attr.hash_entry, digest)
        resource_jar = ctx.actions.declare_file(ctx.label.name + ".resources.jar")
        args = _layout_args(ctx)
        args.add(resource_jar)
        args.add("--modes")
        args.add(json.encode(modes))
        for path in sorted(entries):
            args.add(path)
            args.add(entries[path])
        ctx.actions.run(executable = ctx.executable._pack, arguments = [args], inputs = entries.values(), outputs = [resource_jar], mnemonic = "MxResourceJar")
    args = _layout_args(ctx)
    args.add(output.path)
    if directory:
        args.add("--directory")
    if release_prefix:
        args.add("--archive-prefix")
        args.add(release_prefix)
    if links:
        args.add("--links")
        args.add(json.encode(links))
    if modes:
        args.add("--modes")
        args.add(json.encode(modes))
    if homes and ctx.attr.format == "jar":
        fail("A JDK installation requires a directory or tar layout")
    if homes:
        args.add("--jdk-homes")
        args.add(json.encode([{"bundle": h.bundle.path, "directory": h.directory, "exclude": h.exclude} for h in homes]))
    if tar_trees:
        if ctx.attr.format == "jar":
            fail("A mounted tar tree requires a directory or tar layout")
        args.add("--tar-trees")
        args.add(json.encode([[bundle.path, mount] for bundle, mount in tar_trees]))
    if image_groups:
        if ctx.attr.format == "jar":
            fail("Native Image directories require a directory or tar layout")
        args.add("--image-groups")
        args.add(json.encode([[image.path, kinds, mount] for image, kinds, mount in image_groups]))
    for path in sorted(entries):
        args.add(path)
        args.add(entries[path])
    ctx.actions.run(
        executable = ctx.executable._pack,
        arguments = [args],
        inputs = ctx.files.build_inputs + entries.values() + [h.bundle for h in homes] + [bundle for bundle, _ in tar_trees] + [image for image, _, _ in image_groups],
        outputs = [output],
        mnemonic = "MxReleaseArchive" if release_prefix else "MxArchiveLayout",
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
        MxArchiveLayoutInfo(archive = None if directory else output, entries = entries, links = links, jdk_homes = homes, resource_jar = resource_jar, modes = modes, tar_trees = tar_trees, image_groups = image_groups),
    ] + ([JavaInfo(output_jar = resource_jar, compile_jar = None)] if resource_jar else [])

mx_archive_layout = rule(
    implementation = _layout_impl,
    attrs = {
        # One input can be copied to multiple archive paths.
        "literals": attr.string_dict(),
        "members": attr.label_keyed_string_dict(allow_files = True, mandatory = True),
        # Map a member to a new path, or use an empty selector to import an entire layout.
        "archive_imports": attr.label_keyed_string_dict(providers = [MxArchiveLayoutInfo]),
        "build_inputs": attr.label_list(allow_files = True),
        "tar_trees": attr.label_keyed_string_dict(allow_files = [".tar", ".gz"]),
        "jdk_homes": attr.label_keyed_string_dict(allow_files = [".gz"]),
        "file_list": attr.string_list(),
        "file_list_entry": attr.string(),
        "hash_entry": attr.string(),
        "format": attr.string(mandatory = True, values = ["dir", "jar", "tar"]),
        "target_prefix": attr.string(),
        "release_prefix": attr.string(),
        "use_param_file": attr.bool(),
        "_pack": attr.label(default = Label("//mx:pack_layout"), executable = True, cfg = "exec"),
    },
)

def mx_release_archive(name, install, prefix, **kwargs):
    """Package the resolved standalone for the target OS and architecture."""
    mx_archive_layout(
        name = name,
        members = {},
        archive_imports = {install: "="},
        format = "tar",
        release_prefix = select({
            Label("//graal:standalone_" + key): prefix.replace("<graalvm_os>", os).replace("<arch>", arch)
            for key, os, arch in [
                ("darwin_aarch64", "macos", "aarch64"),
                ("linux_amd64", "linux", "amd64"),
                ("linux_aarch64", "linux", "aarch64"),
            ]
        }),
        **kwargs
    )

def _jdk_resource_impl(ctx):
    tree = ctx.actions.declare_directory(ctx.label.name + ".files")
    jar = ctx.actions.declare_file(ctx.label.name + ".resources.jar")
    manifest = ctx.actions.declare_file(ctx.label.name + ".metadata/files")
    digest = ctx.actions.declare_file(ctx.label.name + ".metadata/sha256")
    recipe = ctx.attr.recipe
    ctx.actions.run(
        executable = ctx.executable._pack,
        arguments = ["--jdk-resource", ctx.file.jdk.path, recipe, tree.path, jar.path, manifest.path, digest.path],
        inputs = [ctx.file.jdk],
        outputs = [tree, jar, manifest, digest],
        mnemonic = "MxJdkResource",
    )
    spec = json.decode(recipe)
    return [
        DefaultInfo(files = depset([tree])),
        JavaInfo(output_jar = jar, compile_jar = None),
        MxArchiveLayoutInfo(
            archive = None,
            entries = {spec["file_list_entry"]: manifest, spec["hash_entry"]: digest},
            resource_jar = jar,
            jdk_homes = [struct(bundle = ctx.file.jdk, directory = spec["directory"], exclude = spec["exclude"])],
            modes = {},
        ),
    ]

_jdk_resource = rule(
    implementation = _jdk_resource_impl,
    attrs = {
        "jdk": attr.label(mandatory = True, allow_single_file = True),
        "recipe": attr.string(mandatory = True),
        "_pack": attr.label(default = Label("//mx:pack_layout"), executable = True, cfg = "exec"),
    },
)

def _overlay_impl(ctx):
    inputs = []
    steps = []
    for item in json.decode(ctx.attr.recipe):
        if "link" in item:
            steps.append(item)
            continue
        dependency = ctx.attr.deps[item.pop("input")]
        layout = dependency[MxArchiveLayoutInfo]
        item["label"] = str(dependency.label)
        if layout.archive:
            inputs.append(layout.archive)
            item["archive"] = layout.archive.path
        else:
            modes = getattr(layout, "modes", {})
            item["files"] = [[n, f.path, modes.get(n, "644")] for n, f in sorted(layout.entries.items())]
            inputs.extend(layout.entries.values())
        steps.append(item)
    recipe = ctx.actions.declare_file(ctx.label.name + ".layout.json")
    if ctx.attr.file_list_entry:
        tree = ctx.actions.declare_directory(ctx.label.name + ".files")
        jar = ctx.actions.declare_file(ctx.label.name + ".resources.jar")
        manifest = ctx.actions.declare_file(ctx.label.name + ".metadata/files")
        digest = ctx.actions.declare_file(ctx.label.name + ".metadata/sha256")
        ctx.actions.write(recipe, json.encode({"steps": steps, "file_list_entry": ctx.attr.file_list_entry, "hash_entry": ctx.attr.hash_entry}))
        ctx.actions.run(
            executable = ctx.executable._pack,
            inputs = inputs + [recipe],
            outputs = [tree, jar, manifest, digest],
            arguments = [recipe.path, tree.path, jar.path, manifest.path, digest.path],
            mnemonic = "MxArchiveResources",
        )
        return [
            DefaultInfo(files = depset([tree])),
            JavaInfo(output_jar = jar, compile_jar = None),
            MxArchiveLayoutInfo(archive = None, resource_jar = jar, entries = {ctx.attr.file_list_entry: manifest, ctx.attr.hash_entry: digest}),
        ]
    ctx.actions.write(recipe, json.encode(steps))
    archive = ctx.actions.declare_file(ctx.label.name.lower().replace("_", "-") + ".tar")
    manifest = ctx.actions.declare_file(archive.basename + ".filelist")
    ctx.actions.run(
        executable = ctx.executable._pack,
        inputs = inputs + [recipe],
        outputs = [archive, manifest],
        arguments = [recipe.path, archive.path, manifest.path],
        mnemonic = "MxInstallOverlay",
    )
    return [
        DefaultInfo(files = depset([archive, manifest])),
        OutputGroupInfo(archive = depset([archive]), file_list = depset([manifest])),
        MxArchiveLayoutInfo(archive = archive, entries = {}, tar_trees = [(archive, "")]),
    ]

_overlay = rule(
    implementation = _overlay_impl,
    attrs = {
        "deps": attr.label_list(providers = [MxArchiveLayoutInfo]),
        "recipe": attr.string(mandatory = True),
        "file_list_entry": attr.string(),
        "hash_entry": attr.string(),
        "_pack": attr.label(default = Label("//mx:install_overlay"), executable = True, cfg = "exec"),
    },
)

def mx_os_layout(name, os_layouts, **kwargs):
    """Select the mx layout using the target operating system."""
    branches = {Label("//tools/cc:profile_" + os) if "_" in os else Label("//tools/cc:linux_musl") if os == "linux-musl" else "@platforms//os:" + os: recipe for os, recipe in os_layouts.items()}
    if all(["overlay" in recipe for recipe in branches.values()]):
        kwargs.pop("format")
        _overlay(
            name = name,
            file_list_entry = select({os: recipe.get("file_list_entry", "") for os, recipe in branches.items()} | {"//conditions:default": ""}),
            hash_entry = select({os: recipe.get("hash_entry", "") for os, recipe in branches.items()} | {"//conditions:default": ""}),
            deps = select({os: recipe["deps"] for os, recipe in branches.items()} | {"//conditions:default": []}),
            recipe = select({os: json.encode(recipe["overlay"]) for os, recipe in branches.items()} | {"//conditions:default": "[]"}),
            target_compatible_with = select({os: [] for os in branches} | {"//conditions:default": ["@platforms//:incompatible"]}),
            **kwargs
        )
        return
    if all(["jdk" in recipe for recipe in branches.values()]):
        kwargs.pop("format")
        _jdk_resource(
            name = name,
            jdk = select({os: recipe["jdk"] for os, recipe in branches.items()}),
            recipe = select({os: json.encode(recipe) for os, recipe in branches.items()}),
            target_compatible_with = select({os: [] for os in branches} | {"//conditions:default": ["@platforms//:incompatible"]}),
            **kwargs
        )
        return
    attrs = {
        key: select({os: recipe.get(key, empty) for os, recipe in branches.items()} | {"//conditions:default": empty})
        for key, empty in [("members", {}), ("archive_imports", {}), ("file_list", []), ("file_list_entry", ""), ("hash_entry", "")]
    }
    mx_archive_layout(
        name = name,
        target_compatible_with = select({os: [] for os in branches} | {"//conditions:default": ["@platforms//:incompatible"]}),
        **(kwargs | attrs)
    )
