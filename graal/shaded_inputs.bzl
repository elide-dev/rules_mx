"""Fetch the binary resources and matching sources pinned by the mx suite."""

def _shaded_inputs(ctx):
    pins = json.decode(ctx.read(ctx.attr.lock))
    for filename, (path, integrity) in pins.items():
        ctx.download(
            url = [
                "https://repo.maven.apache.org/maven2/" + path,
                "https://maven-central.storage-download.googleapis.com/maven2/" + path,
            ],
            output = filename,
            integrity = integrity,
        )
    ctx.file("BUILD.bazel", "exports_files(%s)\n" % repr(sorted(pins)))

shaded_inputs = repository_rule(implementation = _shaded_inputs, attrs = {"lock": attr.label(mandatory = True)})
