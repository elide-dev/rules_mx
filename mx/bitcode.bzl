"""Build Sulong's single-source LLVM IR fixtures with its bitcode variant recipe."""

def _bitcode_impl(ctx):
    obj = ctx.actions.declare_file(ctx.label.name + ".o")
    tool = ctx.attr.llvm_link[DefaultInfo].files_to_run
    for src, out in [(ctx.file.src, obj), (obj, ctx.outputs.out)]:
        args = ctx.actions.args()
        args.add("-o", out)
        args.add(src)
        ctx.actions.run(
            executable = tool,
            inputs = [src],
            tools = ctx.attr.llvm_link[DefaultInfo].files,
            outputs = [out],
            arguments = [args],
            mnemonic = "SulongBitcodeLink",
        )
    return [DefaultInfo(files = depset([ctx.outputs.out]))]

_bitcode = rule(
    implementation = _bitcode_impl,
    attrs = {
        "src": attr.label(allow_single_file = [".ll"], mandatory = True),
        "out": attr.output(mandatory = True),
        "llvm_link": attr.label(executable = True, cfg = "exec", mandatory = True),
    },
)

def mx_bitcode_suite(name, root, cases, llvm_link, testonly = True, visibility = None):
    """Expose each installed .bc output and the complete mx project."""
    targets = []
    for case in cases:
        target = name + "/" + case
        _bitcode(
            name = target,
            src = root + "/" + case,
            out = target + ".dir/bitcode-O0.bc",
            llvm_link = llvm_link,
            testonly = testonly,
            visibility = visibility,
        )
        targets.append(":" + target)
    native.filegroup(name = name, srcs = targets, testonly = testonly, visibility = visibility)
