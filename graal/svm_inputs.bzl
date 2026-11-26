"""Fetch the SubstrateVM libraries that mx downloads by URL, one repository per pin."""

load("@bazel_tools//tools/build_defs/repo:http.bzl", "http_archive", "http_file")

def _svm_inputs_impl(ctx):
    pins = json.decode(ctx.read(Label("//graal:svm_inputs.json")))
    for name, pin in pins.items():
        if pin.get("archive"):
            # Layouts select single members, such as LLVM_LLD_STANDALONE/bin/ld64.lld.
            http_archive(
                name = "graal_svm_" + name,
                urls = pin["urls"],
                integrity = pin["integrity"],
                build_file_content = 'exports_files(glob(["bin/*"]))\n',
            )
        else:
            http_file(
                name = "graal_svm_" + name,
                urls = pin["urls"],
                integrity = pin["integrity"],
                downloaded_file_path = pin["file"],
            )

svm_inputs = module_extension(implementation = _svm_inputs_impl)
