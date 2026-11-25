"""Lay out the source-built Native Image driver and its builder inputs."""

from pathlib import Path
import shutil
import sys
import tarfile


def main(root, native_archive, *args):
    root = Path(root)
    with tarfile.open(native_archive) as archive:
        for parent in ("lib/svm/clibraries", "lib/svm/builder/clibraries"):
            archive.extractall(root / parent, filter="data")
    for name, source in zip(args[::2], args[1::2], strict=True):
        output = root / name
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(source, output)


if __name__ == "__main__":
    main(*sys.argv[1:])
