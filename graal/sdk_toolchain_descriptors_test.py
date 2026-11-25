"""Verify that the SDK compatibility descriptors point to usable compiler binaries."""

from pathlib import Path
import re
import subprocess
import sys
from tempfile import TemporaryDirectory


def ninja_compiler(root):
    lines = (root / "toolchain.ninja").read_text().splitlines()
    command = next(
        line.partition("=")[2].strip()
        for line in reversed(lines)
        if line.startswith("CC=")
    )
    return root / command


def cmake_compiler(root):
    configuration = (root / "toolchain.cmake").read_text()
    match = re.search(
        r"\bset\(CMAKE_C_COMPILER\s+\${CMAKE_CURRENT_LIST_DIR}/([^\s)]+)\)",
        configuration,
    )
    if not match:
        raise AssertionError("Expected a relocatable compiler path in toolchain.cmake")
    return root / match[1]


def main(llvm_ninja, musl_ninja, musl_cmake):
    with TemporaryDirectory() as directory:
        scratch = Path(directory)
        source = scratch / "probe.c"
        source.write_text("int main(void) { return 0; }\n")
        llvm = ninja_compiler(Path(llvm_ninja).absolute())
        subprocess.run(
            [llvm, "-ffreestanding", "-c", source, "-o", scratch / "probe.o"],
            check=True,
        )
        for compiler in (
            ninja_compiler(Path(musl_ninja).absolute()),
            cmake_compiler(Path(musl_cmake).absolute()),
        ):
            program = scratch / "probe"
            subprocess.run([compiler, "-static", source, "-o", program], check=True)
            subprocess.run([program], check=True)


if __name__ == "__main__":
    main(*sys.argv[1:])
