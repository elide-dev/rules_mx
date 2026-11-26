"""Build GraalWasm's Emscripten programs with the BCR Emscripten C++ toolchain."""

load("@emsdk//emscripten_toolchain:wasm_rules.bzl", "wasm_cc_binary")
load("@rules_cc//cc:cc_binary.bzl", "cc_binary")
load("@rules_cc//cc:cc_library.bzl", "cc_library")
load("//graal:layout.bzl", "mx_archive_layout")
load(":project_files.bzl", "mx_project_files", "wabt_translate")

def _index_impl(ctx):
    ctx.actions.write(ctx.outputs.out, "\n".join(ctx.attr.cases) + "\n")

_index = rule(implementation = _index_impl, attrs = {"cases": attr.string_list(), "out": attr.output(mandatory = True)})

def mx_wasm_programs(name, root, cases, sidecars, exports, wat2wasm, wasm2wat, testonly, includes = "", copts = {}, native_cases = [], visibility = None):
    members = {root + "/" + f: f for f in sidecars}
    indices = {}
    deps = []
    if includes:
        headers = name + ".headers"
        cc_library(name = headers, hdrs = native.glob([includes + "/**/*.h"]), includes = [includes], testonly = testonly)
        deps.append(":" + headers)
    for case in cases:
        stem, _, ext = case.rpartition(".")
        folder, _, basename = stem.rpartition("/")
        indices.setdefault(folder, []).append(basename)
        src = root + "/" + case
        prefix = name + ".files/" + stem
        wasm = prefix + ".wasm"
        if ext == "c":
            flags = ["-g2", "-O3"] + copts.get(case, [])
            cc = name + ".cc/" + stem
            cc_binary(name = cc, srcs = [src], deps = deps, copts = flags, linkopts = flags + ["-sSTANDALONE_WASM", "-sWASM_BIGINT", "-sEXPORTED_FUNCTIONS=" + json.encode(exports[case])], testonly = testonly, tags = ["manual"])
            wasm_cc_binary(name = name + "/" + case, cc_target = ":" + cc, outputs = [wasm, prefix + ".js"], testonly = testonly, visibility = visibility)
            wabt_translate(name = name + ".disassemble/" + stem, tool = wasm2wat, src = wasm, out = prefix + ".wat", testonly = testonly)
            for suffix in [".wat", ".wasm", ".js"]:
                members[prefix + suffix] = stem + suffix
            if case in native_cases:
                host = name + ".native/" + stem
                cc_binary(name = host, srcs = [src], copts = flags, deps = deps, linkopts = ["-lm"], testonly = testonly)
                members[":" + host] = folder + "/native/" + basename
        elif ext == "wat":
            wabt_translate(name = name + "/" + case, tool = wat2wasm, src = src, out = wasm, flags = ["--enable-exceptions"], testonly = testonly, visibility = visibility)
            members[src] = case
            members[wasm] = stem + ".wasm"
        else:
            members[src] = case
    for directory, names in indices.items():
        index = (directory + "/" if directory else "") + "wasm_test_index"
        out = name + ".files/" + index
        _index(name = name + ".index/" + index, cases = names, out = out, testonly = testonly)
        members[out] = index
    mx_project_files(name = name, members = members, testonly = testonly, visibility = visibility)
    mx_archive_layout(name = name + ".resources", format = "jar", members = {}, archive_imports = {":" + name: "="}, testonly = testonly)
