"""Resolve each suite-pinned JAR independently when its consuming target is built."""

load("@bazel_tools//tools/build_defs/repo:http.bzl", "http_file")

def _index_impl(ctx):
    ctx.file("BUILD.bazel", "\n".join([
        'alias(name = %s, actual = %s, visibility = ["//visibility:public"])' % (repr(filename), repr(label))
        for filename, label in ctx.attr.labels.items()
    ]) + "\n")

_index = repository_rule(implementation = _index_impl, attrs = {"labels": attr.string_dict()})

def _shaded_inputs_impl(ctx):
    pins = json.decode(ctx.read(Label("//graal:shaded_inputs.json")))
    labels = {}
    for filename, (path, integrity) in pins.items():
        repo = "graal_jar_" + filename
        http_file(
            name = repo,
            urls = path if type(path) == "list" else [
                "https://repo.maven.apache.org/maven2/" + path,
                "https://maven-central.storage-download.googleapis.com/maven2/" + path,
            ],
            integrity = integrity,
            downloaded_file_path = filename,
        )
        labels[filename] = "@" + repo + "//file"
    _index(name = "graal_shaded_inputs", labels = labels)
    return ctx.extension_metadata(reproducible = True)

shaded_inputs = module_extension(implementation = _shaded_inputs_impl)
