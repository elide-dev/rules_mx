"""Archive the two pinned VisualVM subtrees under the mx layout root."""

from copy import copy
import io
from pathlib import Path, PurePosixPath
import posixpath
import sys
import tarfile


def archive_members(archive):
    for member in archive:
        path = PurePosixPath(member.name)
        if path.parts[:2] != ("lib", "visualvm") or len(path.parts) == 2:
            continue
        name = PurePosixPath(*path.parts[2:])
        if ".." in name.parts:
            raise ValueError(f"Invalid VisualVM archive member {member.name}")
        if member.issym():
            resolved = PurePosixPath(posixpath.normpath(name.parent / member.linkname))
            if resolved.is_absolute() or ".." in resolved.parts:
                raise ValueError(f"Invalid VisualVM symlink {member.name}")
        if not (member.isfile() or member.isdir() or member.issym()):
            raise ValueError(f"Unexpected VisualVM archive member {member.name}")
        yield name.as_posix(), (archive, member)


def main(output, common, platform, license_file):
    with (
        tarfile.open(common) as common_archive,
        tarfile.open(platform) as platform_archive,
        tarfile.open(output, "w") as bundle,
    ):
        members = dict(archive_members(common_archive))
        members.update(archive_members(platform_archive))
        for name, (archive, entry) in sorted(members.items()):
            header = copy(entry)
            header.name = name
            header.mtime = 0
            header.uid = header.gid = 0
            header.uname = header.gname = ""
            header.pax_headers = {}
            if entry.isfile():
                with archive.extractfile(entry) as data:
                    bundle.addfile(header, data)
            else:
                bundle.addfile(header)
        data = Path(license_file).read_bytes()
        header = tarfile.TarInfo("LICENSE_VISUALVM.txt")
        header.size = len(data)
        bundle.addfile(header, io.BytesIO(data))


if __name__ == "__main__":
    main(*sys.argv[1:])
