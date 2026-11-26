"""Stage the LLVM programs and runtime files needed by Sulong's CMake recipes."""

from pathlib import Path, PurePosixPath
import re
import sys
import tarfile


def main(archive, root):
    root = Path(root)
    with tarfile.open(archive, "r|*") as bundle:
        for member in bundle:
            path = PurePosixPath(member.name)
            if (
                re.fullmatch(
                    r"bin/(?:clang(?:-\d+|\+\+)?|lli|llvm-link|llvm-config|opt)",
                    str(path),
                )
                or str(path).startswith("lib/clang/")
                or re.fullmatch(
                    r"lib/lib(?:LLVM|LTO|clang-cpp)[^/]*(?:\.dylib|\.so(?:\.[\d.]+)?)",
                    str(path),
                )
            ):
                bundle.extract(member, root, filter="data")
    for name in ("clang", "clang++", "lli", "llvm-link", "llvm-config", "opt"):
        if not (root / "bin" / name).is_file():
            raise ValueError(f"LLVM archive has no bin/{name}")


if __name__ == "__main__":
    main(*sys.argv[1:])
