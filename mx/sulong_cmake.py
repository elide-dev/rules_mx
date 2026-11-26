"""Configure upstream Sulong CMake once per variant and execute Ninja's per-case recipes."""

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile


def configure(spec, build):
    root = Path.cwd()
    sdk = root / spec["sdk"]
    config = root / spec["target_config"]
    bundle = config.parents[1]
    triple = config.stem
    os.environ.update(
        {
            k: v.replace("$EXT_BUILD_ROOT", str(root))
            for k, v in spec["tool_env"].items()
        }
    )
    os.environ["EXT_BUILD_ROOT"] = str(root)
    os.environ["PATH"] = os.pathsep.join(map(str, (sdk / "bin", bundle / "bin")))
    flags = [
        token.replace("<CFGDIR>", str(config.parent))
        for token in shlex.split(config.read_text())
    ]
    flags += [
        "-Qunused-arguments",
        "-B" + str(bundle / "bin"),
        "-L" + str(bundle / "lib" / triple),
        "-ffile-prefix-map=" + str(root) + "=/workspace",
        "-ffile-prefix-map=" + str(build) + "=/build",
        "-fdebug-compilation-dir=.",
    ]
    cxx = [
        "-nostdinc++",
        "-isystem",
        str(bundle / "include" / triple / "c++/v1"),
        "-isystem",
        str(bundle / "include/c++/v1"),
    ]
    settings = dict(spec["config"])
    if spec.get("source_dir"):
        settings["SULONG_TEST_SOURCE_DIR"] = root / spec["source_dir"]
    # Sulong appends CPPFLAGS after individual projects override CMAKE_C_FLAGS.
    os.environ["CPPFLAGS"] = shlex.join(flags)
    settings["CMAKE_CXX_FLAGS"] = shlex.join(
        cxx + shlex.split(settings.get("CMAKE_CXX_FLAGS", ""))
    )
    # Upstream invokes Clang directly for .ll reference programs using these link flags.
    if spec["variant"] == "ref.out" and "LL" in spec["languages"]:
        key = "CMAKE_C_LINK_FLAGS"
        settings[key] = shlex.join(flags + shlex.split(settings.get(key, "")))
    settings.update(
        CMAKE_BUILD_TYPE="Sulong",
        CMAKE_SUPPRESS_REGENERATION="ON",
        CMAKE_SKIP_RPATH="ON",
        CMAKE_MAKE_PROGRAM=root / spec["ninja"],
        CMAKE_MODULE_PATH=root / spec["modules"],
        SULONG_MODULE_PATH=root / spec["modules"],
        SULONG_PROJECT_NAME=spec["project"],
        SULONG_CURRENT_VARIANT=spec["variant"],
        SULONG_BUILD_SHARED_OBJECT="NO",
        SULONG_ENABLED_LANGUAGES=";".join(spec["languages"]),
        CLANG=sdk / "bin/clang",
        CLANGXX=sdk / "bin/clang++",
        LLVM_LINK=sdk / "bin/llvm-link",
        LLVM_CONFIG=sdk / "bin/llvm-config",
        LLVM_OPT=sdk / "bin/opt",
    )
    # Use mx's preload format. A large test matrix can exceed the limit on a single argv element.
    preload = build / "tests.cmake"
    preload.write_text(
        'set(SULONG_TESTS "' + ";".join(spec["plans"]) + '" CACHE STRING "")\n'
    )
    subprocess.run(
        [
            root / spec["cmake"],
            "-S",
            root / spec["source"],
            "-B",
            build,
            "-G",
            "Ninja",
            "-C",
            preload,
            *(f"-D{k}={v}" for k, v in settings.items()),
        ],
        check=True,
    )
    for case, plan in spec["plans"].items():
        name = (
            spec["variant"] if spec["variant"] == "ref.out" else spec["variant"] + ".bc"
        )
        product = case + ".dir/" + name
        entries = json.loads(
            subprocess.check_output(
                [
                    settings["CMAKE_MAKE_PROGRAM"],
                    "-C",
                    build,
                    "-t",
                    "compdb-targets",
                    "-x",
                    product,
                ]
            )
        )
        steps = []
        for entry in entries:
            if not entry["command"]:
                continue
            steps.append(
                [
                    entry["output"],
                    entry["command"]
                    .replace(str(build), "@BUILD@")
                    .replace(str(root), "@EXECROOT@"),
                ]
            )
        if not steps or steps[-1][0] != product:
            raise ValueError(f"No Ninja recipe for {product}")
        Path(plan).write_text(json.dumps(steps) + "\n")


def execute(plan, output, build, unused=None, *headers):
    root = Path.cwd()
    os.environ["PATH"] = ""
    depfiles = []
    for product, command in json.loads(plan.read_text()):
        (build / product).parent.mkdir(parents=True, exist_ok=True)
        # Keep paths tokenized across sandboxes. Only execute the tools declared by this rule.
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|<>")
        lexer.whitespace_split = True
        args = []
        cwd = build
        for token in [*lexer, "&&"]:
            if token != "&&":
                if token in (";", "|", "||", ">", "<", "&"):
                    raise ValueError(
                        f"Unsupported shell operator in CMake recipe: {token}"
                    )
                args.append(
                    token.replace("@BUILD@", str(build)).replace(
                        "@EXECROOT@", str(root)
                    )
                )
                continue
            if args == [":"]:
                pass
            elif args[0] == "cd":
                cwd = (cwd / args[1]).resolve()
                if not cwd.is_relative_to(build):
                    raise ValueError(f"Unexpected CMake working directory: {cwd}")
            elif args[0] == "cp" and len(args) == 3:
                shutil.copyfile(cwd / args[1], cwd / args[2])
            else:
                program = Path(args[0])
                if not program.is_absolute() or not program.is_relative_to(root):
                    raise ValueError(
                        f"CMake selected an undeclared executable: {program}"
                    )
                subprocess.run(args, cwd=cwd, check=True)
                if "-MF" in args:
                    depfiles.append(cwd / args[args.index("-MF") + 1])
            args = []
    shutil.copy2(build / product, output)
    if unused:
        # Only prune headers when Clang recorded the compilation's prerequisites.
        used = set()
        for depfile in depfiles:
            _, deps = depfile.read_text().replace("\\\n", " ").split(":", 1)
            used.update((build / p).resolve() for p in shlex.split(deps))
        Path(unused).write_text(
            "".join(
                h + "\n" for h in headers if depfiles and Path(h).resolve() not in used
            )
        )


if __name__ == "__main__":
    os.environ["SOURCE_DATE_EPOCH"] = "0"
    with tempfile.TemporaryDirectory() as tmp:
        build = Path(tmp).resolve()
        if sys.argv[1] == "configure":
            configure(json.loads(Path(sys.argv[2]).read_text()), build)
        else:
            execute(Path(sys.argv[2]), sys.argv[3], build, *sys.argv[4:])
