"""Inspect the headers, symbols and embedded LLVM code in the installed C++ runtimes."""

from pathlib import Path
import subprocess
import sys


def main(installation, nm, objdump):
    root = Path(installation)
    assert any(root.glob("include/**/c++/v1/vector")), root
    for name, symbol in (("c++", "basic_string"), ("c++abi", "__cxa_demangle"), ("unwind", "_Unwind_RaiseException")):
        libraries = sorted(root.glob(f"lib/**/lib{name}.so.*")) + sorted(root.glob(f"lib/**/lib{name}.*.dylib"))
        assert libraries, (root, name)
        library = libraries[0]
        symbols = subprocess.check_output([nm, "--defined-only", library], text=True)
        assert symbol in symbols, (library, symbol)
        if name == "c++":
            sections = subprocess.check_output([objdump, "--section-headers", library], text=True)
            assert ".llvmbc" in sections or "__bundle" in sections or "__bitcode" in sections, sections


if __name__ == "__main__":
    main(*sys.argv[1:])
