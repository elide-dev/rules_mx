"""Execute a guest from the published GraalWasm archive under its release name."""

import os
from pathlib import Path, PurePosixPath
import platform
import subprocess
import sys
import tarfile
import tempfile


def main():
    path = Path(sys.argv[1])
    edition = sys.argv[2]
    os_name = {"Darwin": "macos", "Linux": "linux"}[platform.system()]
    arch = {"arm64": "aarch64", "aarch64": "aarch64", "x86_64": "amd64"}[
        platform.machine()
    ]
    root_name = path.name.removesuffix(".tar.gz")
    assert root_name.startswith(
        "graalwasm-community-" + ("jvm-" if edition == "jvm" else "")
    )
    assert root_name.endswith(f"-{os_name}-{arch}")
    with tempfile.TemporaryDirectory(dir=os.environ["TEST_TMPDIR"]) as temp:
        dest = Path(temp)
        with tarfile.open(path, "r:gz") as archive:
            members = archive.getmembers()
            assert {PurePosixPath(m.name).parts[0] for m in members} == {root_name}
            assert all((m.uid, m.gid, m.mtime) == (0, 0, 0) for m in members)
            assert archive.getmember(f"{root_name}/bin/wasm").mode == 0o755
            archive.extractall(dest, members=members, filter="data")
        root = dest / root_name
        assert (root / "LICENSE.txt").is_file()
        if edition == "jvm":
            assert (root / "jvm/bin/java").is_file()
            assert not (root / "jvm/jmods").exists()
            assert (root / "jvm/release").read_bytes() == (
                root / "release"
            ).read_bytes()
        guest = dest / "hello.wasm"
        guest.write_bytes(
            bytes.fromhex(
                "0061736d0100000001040160000003020100070a01065f737461727400000a040102000b"
            )
        )
        subprocess.run([root / "bin/wasm", guest], check=True)


if __name__ == "__main__":
    main()
