"""Assemble a pinned SDK compiler payload beside its Ninja or CMake descriptor."""

from pathlib import Path
import shutil
import sys
import tarfile


def main(root, archive, descriptor, name):
    root = Path(root)
    with tarfile.open(archive) as payload:
        payload.extractall(root / "toolchain", filter="data")
    shutil.copyfile(descriptor, root / name)


if __name__ == "__main__":
    main(*sys.argv[1:])
