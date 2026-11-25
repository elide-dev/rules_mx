"""Write Native Image static library symbol manifests, as mx's StaticLibrarySymbolsBuilder does.

Each manifest lists `dependency:<library>` records, then the exported symbols that Native Image
links against. The SVM libjvm replacement has no dependencies. A JDK archive takes its
dependencies from the NEEDED entries of its dynamic counterpart in the same JDK.
"""

import argparse
from pathlib import Path
import re
import subprocess

JNI = ("JNI_OnLoad_", "JNI_OnUnload_", "Java_")
LIBJAVA = ("JDK_", "JNU_", "jio_", "GetStringPlatformChars", "getErrorString")
LIBJAVA += ("getEncodingFromLangID", "getJavaIDFromLangID")
SVM_JVM = ("JNI_CreateJavaVM", "JNI_GetCreatedJavaVMs", "JNI_GetDefaultJavaVMInitArgs")


def library_name(path):
    name = Path(path).name
    name = name.removeprefix("lib")
    for suffix in (".a", ".so", ".dylib"):
        name = name.removesuffix(suffix)
    return name


def symbols(nm, archive, prefixes, darwin):
    found = set()
    listing = subprocess.run(
        [nm, "-g", archive], check=True, capture_output=True, text=True
    ).stdout
    for line in listing.splitlines():
        tokens = line.split()
        if len(tokens) < 2 or tokens[-2] not in ("T", "W"):
            continue
        symbol = tokens[-1]
        if darwin and symbol.startswith("_"):
            symbol = symbol[1:]
        if symbol.startswith(prefixes):
            found.add(symbol)
    return found


def needed(objdump, dynamic):
    headers = subprocess.run(
        [objdump, "-p", dynamic], check=True, capture_output=True, text=True
    ).stdout
    elf = re.findall(r"(?m)^\s*NEEDED\s+(\S+)", headers)
    macho = re.findall(r"(?m)^\s*name\s+(\S+)\s+\(offset", headers)
    return {library_name(entry) for entry in elf + macho}


def write(path, dependencies, names):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(
        "".join(f"dependency:{d}\n" for d in sorted(dependencies))
        + "".join(f"{s}\n" for s in sorted(names))
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--nm", required=True)
    parser.add_argument("--objdump")
    parser.add_argument("--darwin", action="store_true")
    parser.add_argument("--java-home", help="find JDK dynamic counterparts here")
    parser.add_argument("--svm-jvm", nargs=2, metavar=("ARCHIVE", "OUTPUT"))
    parser.add_argument(
        "--jdk", nargs=2, action="append", default=[], metavar=("ARCHIVE", "OUTPUT")
    )
    parser.add_argument(
        "--jdk-jvm", help="the JDK's own static libjvm, a dependency target only"
    )
    args = parser.parse_args()
    if args.svm_jvm:
        archive, output = args.svm_jvm
        found = symbols(args.nm, archive, JNI + ("JVM_", "jio_"), args.darwin)
        write(output, (), found | set(SVM_JVM))
    available = {library_name(a) for a, _ in args.jdk}
    if args.jdk_jvm:
        available.add(library_name(args.jdk_jvm))
    for archive, output in args.jdk:
        name = library_name(archive)
        prefixes = JNI + LIBJAVA if name == "java" else JNI
        found = symbols(args.nm, archive, prefixes, args.darwin)
        if name == "java":
            found |= {"jio_fprintf", "jio_snprintf"}
        suffix = ".dylib" if args.darwin else ".so"
        home = Path(args.java_home)
        dynamic = next(
            (
                home / d / f"lib{name}{suffix}"
                for d in ("lib", "lib/server", "bin", "bin/server")
                if (home / d / f"lib{name}{suffix}").is_file()
            ),
            None,
        )
        if dynamic is None:
            if found:
                raise SystemExit(f"{archive} exports JNI symbols but has no dynamic library")
            dependencies = set()
        else:
            dependencies = (needed(args.objdump, dynamic) & available) - {name}
        write(output, dependencies, found)


if __name__ == "__main__":
    main()
