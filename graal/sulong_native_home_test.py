"""Verify the installed Sulong runtime archive and the inventory consumed by mx."""

from pathlib import Path, PurePosixPath
import sys
import tarfile


def main(archive, file_list):
    with tarfile.open(archive) as src:
        files = {m.name: m for m in src if not m.isdir()}
        manifest = Path(file_list).read_text().splitlines()
        assert len(files) == len(manifest) and set(files) == set(manifest)
        assert "native/cmake/toolchain.cmake" in files
        assert any(n.endswith("/c++/v1/vector") for n in files)
        assert not any(n.startswith("native/lib/") and n.endswith(".a") for n in files)
        alias, = (m for n, m in files.items() if n.startswith("native/lib/libpolyglot-mock."))
        assert alias.issym()
        link = files[str(PurePosixPath(alias.name).parent / alias.linkname)]
        assert link.issym()
        library = files[str(PurePosixPath(link.name).parent / link.linkname)]
        assert library.isfile() and library.size
        assert any(n.startswith("native/lib/libsulong-native.") for n in files)


if __name__ == "__main__":
    main(*sys.argv[1:])
