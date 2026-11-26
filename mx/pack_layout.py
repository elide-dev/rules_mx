"""Create deterministic mx layout archives from declared Bazel inputs."""

from pathlib import Path, PurePosixPath
import shutil
import sys
import tarfile
import zipfile


def main():
    output = Path(sys.argv[1])
    args = sys.argv[2:]
    tree = None
    if args[:1] == ["--source-tree"]:
        tree = Path(args[1])
        args = args[2:]
    entries = list(zip(args[::2], map(Path, args[1::2]), strict=True))
    for name, _ in entries:
        path = PurePosixPath(name)
        if (
            path.is_absolute()
            or ".." in path.parts
            or not path.parts
            or str(path) != name
        ):
            raise ValueError(f"Invalid archive path: {name}")

    if tree:
        for name, source in entries:
            destination = tree / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)

    if output.suffix in (".jar", ".zip"):
        with zipfile.ZipFile(output, "w") as archive:
            for name, source in entries:
                entry = zipfile.ZipInfo(name)
                entry.external_attr = 0o100644 << 16
                archive.writestr(entry, source.read_bytes())
    else:
        with tarfile.open(output, "w") as archive:
            for name, source in entries:
                entry = tarfile.TarInfo(name)
                entry.size = source.stat().st_size
                entry.mode = 0o755 if source.stat().st_mode & 0o111 else 0o644
                with source.open("rb") as file:
                    archive.addfile(entry, file)


if __name__ == "__main__":
    main()
