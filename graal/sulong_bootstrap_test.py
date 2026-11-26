"""Compile and archive an object using the extracted Sulong bootstrap toolchain."""

import os
from pathlib import Path
import subprocess
import sys
import tarfile


def main(archive_path):
    root = Path(os.environ["TEST_TMPDIR"]) / "relocated bootstrap"
    with tarfile.open(archive_path) as archive:
        archive.extractall(root, filter="data")
    assert (root / "cmake/toolchain.cmake").is_file()
    ninja = (root / "toolchain.ninja").read_text()
    for variable in ("CC", "CXX", "AR"):
        path = next(
            line.split(" = ", 1)[1]
            for line in reversed(ninja.splitlines())
            if line.startswith(variable + " = ")
        )
        assert (root / path).is_file(), path
    source = root / "example.c"
    source.write_text("int bootstrap_probe(void) { return 42; }\n", encoding="utf-8")
    subprocess.run(
        [root / "bin/cc", "-c", source, "-o", root / "example.o"], check=True
    )
    # The bootstrap driver may emit raw bitcode or embed it in an object.
    header = (root / "example.o").read_bytes()[:4]
    if header not in (b"BC\xc0\xde", b"\xde\xc0\x17\x0b"):
        sections = subprocess.check_output(
            [root / "bin/llvm-objdump", "-h", root / "example.o"], text=True
        )
        assert ".llvmbc" in sections or "__bitcode" in sections, sections
    subprocess.run(
        [root / "bin/ar", "rcs", root / "example.a", root / "example.o"], check=True
    )
    symbols = subprocess.check_output(
        [root / "bin/llvm-nm", root / "example.a"], text=True
    )
    assert "bootstrap_probe" in symbols, symbols


if __name__ == "__main__":
    main(sys.argv[1])
