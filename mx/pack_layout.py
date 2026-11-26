"""Create deterministic mx layout archives from declared Bazel inputs."""

import json
from pathlib import Path, PurePosixPath
import shutil
import sys
import tarfile
import zipfile


def main():
    output = Path(sys.argv[1])
    args = sys.argv[2:]
    directory = args[:1] == ["--directory"]
    if directory:
        args = args[1:]
    links = {}
    if args[:1] == ["--links"]:
        links = json.loads(args[1])
        args = args[2:]
    tree = output if directory else None
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
            if name in links:
                destination.symlink_to(links[name])
            else:
                shutil.copyfile(source, destination)
                if directory:
                    destination.chmod(0o755 if source.stat().st_mode & 0o111 else 0o644)
    if directory:
        return

    if output.suffix in (".jar", ".zip"):
        with zipfile.ZipFile(output, "w") as archive:
            for name, source in entries:
                entry = zipfile.ZipInfo(name)
                entry.external_attr = (0o120777 if name in links else 0o100644) << 16
                archive.writestr(
                    entry,
                    links[name].encode() if name in links else source.read_bytes(),
                )
    else:
        with tarfile.open(output, "w") as archive:
            for name, source in entries:
                entry = tarfile.TarInfo(name)
                if name in links:
                    entry.type = tarfile.SYMTYPE
                    entry.linkname = links[name]
                    entry.mode = 0o777
                    archive.addfile(entry)
                    continue
                entry.size = source.stat().st_size
                entry.mode = 0o755 if source.stat().st_mode & 0o111 else 0o644
                with source.open("rb") as file:
                    archive.addfile(entry, file)


if __name__ == "__main__":
    main()
