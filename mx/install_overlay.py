"""Assemble mx layouts whose archive member names become available during execution."""

import fnmatch
import json
from pathlib import Path, PurePosixPath
import shutil
import sys
import tarfile
import tempfile

from pack_layout import _package_resources, _relative, _write_tree_archive


def assemble(spec, archive, file_list, resource=None):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(archive).absolute() if resource else Path(tmp).resolve() / "install"
        root.mkdir(exist_ok=True)
        installed = {}
        for op in spec:
            dest = op["destination"]
            if op.get("link"):
                if dest in installed:
                    raise ValueError(f"Duplicate installed member {dest}")
                path = root / _relative(dest)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.symlink_to(_relative(op["link"]))
                installed[dest] = None
                continue
            pattern = op["pattern"]
            prefix = pattern.rpartition("/")[0]
            count = 0
            def select(member):
                nonlocal count
                if not fnmatch.fnmatchcase(member, pattern) or any(fnmatch.fnmatchcase(member, x) for x in op["exclude"]):
                    return None
                name = str(PurePosixPath(member).relative_to(prefix)) if prefix else member
                if dest and not dest.endswith("/"):
                    name = dest
                else:
                    name = dest + name
                path = root / _relative(name)
                if name in installed:
                    raise ValueError(f"Duplicate installed member {name}")
                installed[name] = None
                path.parent.mkdir(parents=True, exist_ok=True)
                count += 1
                return path
            for name, file, mode in op.get("files", []):
                path = select(name)
                if path:
                    shutil.copyfile(file, path)
                    path.chmod(int(mode, 8))
            if "archive" in op:
                with tarfile.open(op["archive"]) as src:
                    for member in src:
                        if member.isdir():
                            continue
                        path = select(member.name)
                        if path is None:
                            continue
                        if op.get("dereference"):
                            visited = set()
                            while member.issym() or member.islnk():
                                if member.name in visited:
                                    raise ValueError(f"Cyclic link {member.name}")
                                visited.add(member.name)
                                link = PurePosixPath(member.linkname)
                                target = PurePosixPath(member.name).parent / link if member.issym() else link
                                member = src.getmember(str(_relative(target.as_posix())))
                        if member.issym():
                            # Keep the relative symlink under the assembled installation.
                            if not (path.parent / member.linkname).resolve().is_relative_to(root):
                                raise ValueError(f"Escaping link {member.name} -> {member.linkname}")
                            path.symlink_to(member.linkname)
                        elif member.isfile() or member.islnk():
                            with src.extractfile(member) as source, path.open("wb") as out:
                                shutil.copyfileobj(source, out)
                            path.chmod(member.mode & 0o777)
                        else:
                            raise ValueError(f"Unsupported archive member {member.name}")
            if not count:
                raise ValueError(f"No install inputs matched {pattern} in {op['label']}")
        if resource:
            metadata, jar, manifest, digest = resource
            _package_resources(root, metadata, jar, manifest, digest)
        else:
            _write_tree_archive(root, archive)
            file_list.write_text("\n".join(installed))


if __name__ == "__main__":
    recipe = json.loads(Path(sys.argv[1]).read_text())
    if len(sys.argv) == 6:
        directory, jar, manifest, digest = map(Path, sys.argv[2:])
        assemble(recipe["steps"], directory, None, (recipe, jar, manifest, digest))
    else:
        assemble(recipe, Path(sys.argv[2]), Path(sys.argv[3]))
