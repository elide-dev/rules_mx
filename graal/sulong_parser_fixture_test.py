"""Verify the packaged GCC parser cases and disassemble a multi-source case."""

from pathlib import Path
import subprocess
import sys
import tarfile


def main(archive, sdk, bitcode):
    member = "gcc-5.2.0/gcc/testsuite/gcc.c-torture/compile/20031023-2.c.dir/bitcode-O0.bc"
    with tarfile.open(archive) as bundle:
        files = [entry for entry in bundle if entry.isfile()]
        assert files and all(entry.name.endswith(".c.dir/bitcode-O0.bc") for entry in files)
        assert len(files) == len({entry.name for entry in files})
        assert bundle.extractfile(member).read() == Path(bitcode).read_bytes()
        for entry in files:
            assert bundle.extractfile(entry).read(4) == b"BC\xc0\xde", entry.name
    subprocess.run([Path(sdk) / "bin/opt", "-passes=verify", "-disable-output", bitcode], check=True)


if __name__ == "__main__":
    main(*sys.argv[1:])
