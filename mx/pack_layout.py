"""Create deterministic mx layout archives from declared Bazel inputs."""

from contextlib import ExitStack
import fnmatch
import gzip
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import tarfile
import tempfile
import zipfile


def _relative(name):
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts or str(path) != name:
        raise ValueError(f"Invalid layout path: {name}")
    return path


def _install_jdk(tree, spec):
    destination = tree / _relative(spec["directory"].rstrip("/"))
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(spec["bundle"]) as bundle:
        contents = bundle.getmembers()
        roots = [
            PurePosixPath(member.name).parent
            for member in contents
            if member.isfile()
            and PurePosixPath(member.name).name == "release"
            and len(PurePosixPath(member.name).parts) in (2, 4)
        ]
        if len(roots) != 1:
            raise ValueError(f"Expected one Java home in {spec['bundle']}")
        root = roots[0]
        extension = ".dylib" if root.parts[-2:] == ("Contents", "Home") else ".so"
        excludes = [
            re.sub(r"<lib:([\w.-]+)>", r"lib\1" + extension, expr)
            for expr in spec["exclude"]
        ]

        def select(member, directory):
            path = PurePosixPath(member.name)
            if not path.is_relative_to(root) or path == root:
                return None
            relative = path.relative_to(root)
            _relative(str(relative))
            if any(
                fnmatch.fnmatchcase(str(part), pattern)
                for part in (relative, *relative.parents)
                for pattern in excludes
            ):
                return None
            target = destination / relative
            if (target.is_symlink() or target.exists()) and not member.isdir():
                raise ValueError(f"Duplicate JDK member {relative}")
            link = member.linkname
            if member.islnk():
                link = str(PurePosixPath(link).relative_to(root))
                _relative(link)
            return tarfile.data_filter(
                member.replace(name=str(relative), linkname=link), directory
            )

        bundle.extractall(destination, members=contents, filter=select)


def _write_tree_archive(tree, output, prefix=None):
    with ExitStack() as stack:
        stream = stack.enter_context(output.open("wb"))
        if output.name.endswith(".tar.gz"):
            stream = stack.enter_context(
                gzip.GzipFile(filename="", fileobj=stream, mode="wb", mtime=0)
            )
        archive = stack.enter_context(tarfile.open(fileobj=stream, mode="w"))
        paths = sorted(tree.rglob("*"))
        if prefix:
            paths.insert(0, tree)
        for path in paths:
            relative = path.relative_to(tree)
            name = prefix / relative if prefix else relative
            member = archive.gettarinfo(str(path), arcname=name.as_posix())
            member.uid = member.gid = member.mtime = 0
            member.uname = member.gname = ""
            if member.isfile():
                member.mode = 0o755 if member.mode & 0o111 else 0o644
                with path.open("rb") as src:
                    archive.addfile(member, src)
            else:
                member.mode = 0o777 if member.issym() else 0o755
                archive.addfile(member)


def main():
    output = Path(sys.argv[1])
    args = sys.argv[2:]
    directory = args[:1] == ["--directory"]
    if directory:
        args = args[1:]
    prefix = None
    if args[:1] == ["--archive-prefix"]:
        prefix = _relative(args[1])
        args = args[2:]
    links = {}
    if args[:1] == ["--links"]:
        links = json.loads(args[1])
        args = args[2:]
    homes = []
    if args[:1] == ["--jdk-homes"]:
        homes = json.loads(args[1])
        args = args[2:]
    tree = output if directory else None
    if args[:1] == ["--source-tree"]:
        tree = Path(args[1])
        args = args[2:]
    entries = list(zip(args[::2], map(Path, args[1::2]), strict=True))
    for name, _ in entries:
        _relative(name)

    with ExitStack() as stack:
        if (homes or prefix) and not tree:
            tree = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        if tree:
            tree.mkdir(parents=True, exist_ok=True)
            for name, source in entries:
                destination = tree / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                if name in links:
                    destination.symlink_to(links[name])
                else:
                    shutil.copyfile(source, destination)
                    if directory or homes or prefix:
                        destination.chmod(
                            0o755 if source.stat().st_mode & 0o111 else 0o644
                        )
            for home in homes:
                _install_jdk(tree, home)
            if directory:
                return
            if homes or prefix:
                _write_tree_archive(tree, output, prefix)
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
