"""Check the installed Sulong CMake libraries and their embedded guest bitcode."""

from pathlib import Path
import subprocess
import sys


def main(nm, objdump, graal, native):
    for library, symbol in [
        (graal, "polyglot_import"),
        (native, "__sulong_posix_open"),
    ]:
        symbols = subprocess.check_output([nm, "--defined-only", library], text=True)
        assert any(
            row.split()[-1].lstrip("_") == symbol.lstrip("_")
            for row in symbols.splitlines()
            if row.strip()
        ), (library, symbol)
        sections = subprocess.check_output([objdump, "-h", library], text=True)
        assert ".llvmbc" in sections or "__bitcode" in sections, (library, sections)
    assert Path(graal).name in ("libgraalvm-llvm.so.1", "libgraalvm-llvm.1.dylib")
    assert Path(native).name in ("libsulong-native.so", "libsulong-native.dylib")


if __name__ == "__main__":
    main(*sys.argv[1:])
