"""Run the upstream SDK C and C++ compiler probes with the selected toolchain."""

load("@rules_cc//cc:cc_binary.bzl", "cc_binary")
load("@rules_python//python:py_test.bzl", "py_test")

def sdk_toolchain_test(name, sources):
    binaries = []
    for src in sources:
        binary = name + "." + src.rsplit("/", 1)[-1]
        cc_binary(name = binary, srcs = [src], testonly = True)
        binaries.append(":" + binary)
    py_test(
        name = name,
        size = "small",
        srcs = [Label("//graal:toolchain_test.py")],
        main = Label("//graal:toolchain_test.py"),
        args = ["$(rootpath %s)" % binary for binary in binaries],
        data = binaries,
        visibility = ["//visibility:public"],
    )
