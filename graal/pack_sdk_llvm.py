"""Repack the pinned LLVM distribution with mx's SDK exclusions."""

import argparse
from copy import copy
from fnmatch import fnmatchcase
import gzip
from pathlib import Path, PurePosixPath
import posixpath
import sys
import tarfile


def excluded(path, patterns):
    return any(
        len(pattern) <= len(path.parts)
        and all(
            (glob.startswith(".") or not part.startswith("."))
            and fnmatchcase(part, glob)
            for part, glob in zip(path.parts, pattern)
        )
        for pattern in patterns
    )


def copy_archive(bundle, source, patterns, dereference, seen):
    with tarfile.open(source) as archive:
        for member in archive:
            path = PurePosixPath(member.name)
            if not path.parts:
                continue
            if path.is_absolute() or ".." in path.parts:
                raise ValueError(f"Invalid LLVM archive member {member.name}")
            if path.parts[0].startswith(".") or excluded(path, patterns):
                continue
            if member.issym() or member.islnk():
                parent = path.parent if member.issym() else PurePosixPath()
                target = PurePosixPath(posixpath.normpath(parent / member.linkname))
                if target.is_absolute() or ".." in target.parts:
                    raise ValueError(f"Invalid LLVM link {member.name}")
            elif not (member.isdir() or member.isfile()):
                raise ValueError(f"Unsupported LLVM member type {member.name}")
            if member.isdir() and path in seen:
                continue
            if path in seen:
                raise ValueError(f"Duplicate LLVM archive member {member.name}")
            seen.add(path)
            if dereference and (member.issym() or member.islnk()):
                data = archive.extractfile(member)
                entry = tarfile.TarInfo(path.as_posix())
                entry.mode = member.mode
                entry.size = data.seek(0, 2)
                data.seek(0)
                with data:
                    bundle.addfile(entry, data)
                continue
            entry = copy(member)
            entry.name = path.as_posix()
            entry.mtime = 0
            entry.uid = entry.gid = 0
            entry.uname = entry.gname = ""
            entry.pax_headers = {}
            if entry.isfile():
                with archive.extractfile(member) as data:
                    bundle.addfile(entry, data)
            else:
                bundle.addfile(entry)


def main(args):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output")
    parser.add_argument("llvm")
    parser.add_argument("compiler_rt")
    parser.add_argument("--exclude", action="append", default=[])
    parser.add_argument("--dereference", action="store_true")
    parser.add_argument("--resource", nargs=2, action="append", default=[])
    options = parser.parse_args(args)
    with (
        Path(options.output).open("wb") as output,
        gzip.GzipFile(
            filename="", fileobj=output, mode="wb", compresslevel=1, mtime=0
        ) as compressed,
        tarfile.open(fileobj=compressed, mode="w|") as bundle,
    ):
        seen = set()
        patterns = [PurePosixPath(pattern).parts for pattern in options.exclude]
        copy_archive(bundle, options.llvm, patterns, options.dereference, seen)
        copy_archive(bundle, options.compiler_rt, [], options.dereference, seen)
        for destination, source in sorted(options.resource):
            entry = tarfile.TarInfo(destination)
            entry.size = Path(source).stat().st_size
            with Path(source).open("rb") as data:
                bundle.addfile(entry, data)


if __name__ == "__main__":
    main(sys.argv[1:])
