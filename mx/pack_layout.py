"""Create deterministic mx layout archives from declared Bazel inputs."""

from contextlib import ExitStack
import fnmatch
import gzip
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import tarfile
import tempfile
import zipfile


def _relative(name):
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts or str(path) != name:
        raise ValueError(f"Invalid layout path: {name}")
    return path


def _install_jdk(tree, spec, explicit_members):
    destination = (
        tree / _relative(spec["directory"].rstrip("/")) if spec["directory"] else tree
    )
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
            re.sub(
                r"<lib:([\w.-]+)>",
                r"lib\1" + extension,
                expr.replace("<jdk_lib_dir>", "lib"),
            )
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
            if not spec["directory"] and relative.as_posix() in explicit_members:
                # A standalone overlays the guest JDK release with its assembled metadata.
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


def _write_tree_archive(tree, output, prefix=None, dereference=False):
    with ExitStack() as stack:
        stream = stack.enter_context(output.open("wb"))
        if output.name.endswith(".tar.gz"):
            stream = stack.enter_context(
                gzip.GzipFile(filename="", fileobj=stream, mode="wb", mtime=0)
            )
        archive = stack.enter_context(tarfile.open(fileobj=stream, mode="w", dereference=dereference))
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


def _resource_metadata(manifest, digest_path, args):
    replacements = {
        "\t": "\\t",
        "\n": "\\n",
        **{char: "\\" + char for char in "\\:=#! "},
    }
    digest = hashlib.sha256()
    lines = []
    for member, filename, mode in zip(args[::3], args[1::3], args[2::3], strict=True):
        _relative(member)
        file = Path(filename)
        with file.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        digest.update(member.encode())
        bits = file.stat().st_mode if mode == "source" else int(mode, 8)
        escaped = "".join(
            replacements.get(char, char if char.isascii() else f"\\u{ord(char):04x}")
            for char in member
        )
        lines.append(f"{escaped} = {stat.filemode(bits)[1:]}")
    Path(manifest).write_bytes("\n".join(lines).encode())
    Path(digest_path).write_bytes(digest.hexdigest().encode())


def _jdk_resource(bundle, recipe, directory, jar, manifest, digest):
    spec = json.loads(recipe)
    tree = Path(directory)
    prefix = _relative(spec["directory"])
    _install_jdk(tree, {"directory": str(prefix), "exclude": spec["exclude"], "bundle": bundle}, set())
    members = []
    for file in sorted((tree / prefix).rglob("*")):
        if file.is_symlink():
            target = file.resolve(strict=True)
            if not target.is_relative_to((tree / prefix).resolve()) or not target.is_file():
                raise ValueError(f"Unexpected JDK resource link {file}")
            file.unlink()
            shutil.copy2(target, file)
        if file.is_file():
            members.append((file.relative_to(tree).as_posix(), file))
    _resource_metadata(manifest, digest, [arg for name, file in members for arg in (name, file, "source")])
    for key, generated in (("file_list_entry", manifest), ("hash_entry", digest)):
        name = str(_relative(spec[key]))
        location = tree / name
        if location.exists():
            raise ValueError(f"Duplicate resource metadata {name}")
        shutil.copyfile(generated, location)
        members.append((name, location))
    with zipfile.ZipFile(jar, "w") as archive:
        for name, file in members:
            entry = zipfile.ZipInfo(name)
            entry.external_attr = (stat.S_IFREG | (file.stat().st_mode & 0o777)) << 16
            with file.open("rb") as src, archive.open(entry, "w") as dst:
                shutil.copyfileobj(src, dst)


def _install_image_group(tree, image, kinds, mount):
    root = Path(image)
    destination = tree / _relative(mount.rstrip("/")) if mount else tree
    manifest = json.loads((root / "build-artifacts.json").read_text())
    for kind in kinds:
        for member in manifest.get(kind, []):
            source = root / _relative(member)
            for file in sorted(source.rglob("*")) if source.is_dir() else [source]:
                if file.is_dir():
                    continue
                target = destination / file.relative_to(root)
                if target.exists() or target.is_symlink():
                    raise ValueError(f"Duplicate image artifact {target}")
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(file, target)


def main():
    if sys.argv[1] == "--materialize-install-links":
        tree, sources, build = [Path(arg).resolve() for arg in sys.argv[2:]]
        source_files = None
        for link in tree.rglob("*"):
            if not link.is_symlink():
                continue
            target = link.resolve(strict=True)
            if not link.readlink().is_absolute() and target.is_relative_to(tree):
                continue
            # Darwin CMake can install a sandbox input symlink as a header. Check
            # the resolved file against the declared source tree before copying it.
            if source_files is None:
                source_files = {file.resolve() for file in sources.rglob("*") if file.is_file()}
            if not target.is_file() or (target not in source_files and not target.is_relative_to(build) and not target.is_relative_to(tree)):
                raise ValueError(f"Unexpected CMake install link {link} -> {target}")
            link.unlink()
            shutil.copy2(target, link)
        return
    if sys.argv[1] == "--archive-file-tree":
        output, prefix, tree = sys.argv[2:]
        _write_tree_archive(Path(tree), Path(output), _relative(prefix), dereference=True)
        return
    if sys.argv[1] == "--image-primary":
        image, out = map(Path, sys.argv[2:])
        shutil.copy2(image / out.name, out)
        return
    if sys.argv[1] == "--jdk-resource":
        _jdk_resource(*sys.argv[2:])
        return
    if sys.argv[1] == "--metadata":
        _resource_metadata(sys.argv[2], sys.argv[3], sys.argv[4:])
        return
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
    modes = {}
    if args[:1] == ["--modes"]:
        modes = {path: int(mode, 8) for path, mode in json.loads(args[1]).items()}
        args = args[2:]
    homes = []
    if args[:1] == ["--jdk-homes"]:
        homes = json.loads(args[1])
        args = args[2:]
    tar_trees = []
    if args[:1] == ["--tar-trees"]:
        tar_trees = json.loads(args[1])
        args = args[2:]
    image_groups = []
    if args[:1] == ["--image-groups"]:
        image_groups = json.loads(args[1])
        args = args[2:]
    tree = output if directory else None
    if args[:1] == ["--source-tree"]:
        tree = Path(args[1])
        args = args[2:]
    entries = list(zip(args[::2], map(Path, args[1::2]), strict=True))
    for name, _ in entries:
        _relative(name)

    with ExitStack() as stack:
        if (homes or tar_trees or image_groups or prefix) and not tree:
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
                            modes.get(
                                name, 0o755 if source.stat().st_mode & 0o111 else 0o644
                            )
                        )
            for image, kinds, mount in image_groups:
                _install_image_group(tree, image, kinds, mount)
            for filename, mount in tar_trees:
                destination = tree / _relative(mount.rstrip("/"))
                if destination.exists():
                    raise ValueError(f"Archive mount already populated: {mount}")
                destination.mkdir(parents=True)
                with tarfile.open(filename) as archive:
                    archive.extractall(destination, filter="data")
            for home in homes:
                _install_jdk(tree, home, {name for name, _ in entries})
            if directory:
                return
            if homes or tar_trees or image_groups or prefix:
                _write_tree_archive(tree, output, prefix)
                return

    if output.suffix in (".jar", ".zip"):
        with zipfile.ZipFile(output, "w") as archive:
            for name, source in entries:
                entry = zipfile.ZipInfo(name)
                entry.external_attr = (
                    0o120777 if name in links else stat.S_IFREG | modes.get(name, 0o644)
                ) << 16
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
                entry.mode = modes.get(
                    name, 0o755 if source.stat().st_mode & 0o111 else 0o644
                )
                with source.open("rb") as file:
                    archive.addfile(entry, file)


if __name__ == "__main__":
    main()
