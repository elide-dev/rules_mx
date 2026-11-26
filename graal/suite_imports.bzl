"""Pinned external mx suite definitions used by Graal graph discovery."""

def _metadata_impl(ctx):
    manifest = ctx.read(ctx.attr.manifest)
    for name, pin in json.decode(manifest).items():
        project = pin["repository"].removeprefix("https://github.com/").removesuffix(".git")
        for path, digest in pin["files"].items():
            ctx.download(
                url = "https://raw.githubusercontent.com/%s/%s/%s" % (project, pin["revision"], path),
                output = name + "/" + path,
                sha256 = digest,
            )
    ctx.file("imports.json", manifest)
    ctx.file("BUILD.bazel", 'exports_files(["imports.json"])')

_metadata = repository_rule(
    implementation = _metadata_impl,
    attrs = {"manifest": attr.label(mandatory = True)},
)

def _imports_impl(ctx):
    _metadata(name = "graal_suite_metadata", manifest = Label("//graal:suite_imports.json"))
    return ctx.extension_metadata(reproducible = True)

suite_imports = module_extension(implementation = _imports_impl)
