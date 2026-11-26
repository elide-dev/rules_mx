"""Extract llvm-link and its shared LLVM library from the selected Graal SDK."""

from pathlib import Path, PurePosixPath
import shutil
import sys
import tarfile


def main(archive, executable, library_dir):
    libraries = Path(library_dir)
    libraries.mkdir(exist_ok=True)
    links = {}
    with tarfile.open(archive, "r|*") as bundle:
        for member in bundle:
            path = PurePosixPath(member.name)
            if path.as_posix() == "bin/llvm-link":
                target = Path(executable)
            elif path.parent == PurePosixPath("lib") and (
                path.name.startswith("libLLVM")
                and (".so" in path.suffixes or path.suffix == ".dylib")
            ):
                target = libraries / path.name
                if member.issym() or member.islnk():
                    links[target] = libraries / PurePosixPath(member.linkname).name
                    continue
            else:
                continue
            if not member.isfile():
                raise ValueError(f"Unexpected SDK archive entry {member.name}")
            with bundle.extractfile(member) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            target.chmod(0o755)
    for target, source in links.items():
        target.symlink_to(source.name)
    if not Path(executable).is_file() or any(not target.is_file() for target in links):
        raise ValueError("Incomplete LLVM linker in SDK archive")


if __name__ == "__main__":
    main(*sys.argv[1:])
