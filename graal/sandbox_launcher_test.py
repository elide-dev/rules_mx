"""Exercise the mx tool aliases from a packaged, source-built native launcher."""

from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile


def main(archive, sdk):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        with tarfile.open(archive) as tar:
            tar.extractall(root, filter="data")
        binary = root / "musl/bin/cc"
        assert binary.readlink() == Path("graalvm-musl-swcfi-clang")
        assert binary.with_name("graalvm-musl-swcfi-clang").readlink() == Path(
            "graalvm-musl-swcfi-tool"
        )
        (root / "llvm").symlink_to(Path(sdk).resolve(), target_is_directory=True)
        # The SDK clang supports stock musl. The swcfi target requires the sandbox LLVM fork.
        target = "--target=x86_64-unknown-linux-musl"
        subprocess.run(
            [binary, target, "-x", "c", "-c", "-", "-o", "member.o"],
            input=b"int member(void) { return 42; }\n",
            cwd=root,
            check=True,
        )
        assert (root / "member.o").read_bytes().startswith(b"\x7fELF")


if __name__ == "__main__":
    main(*sys.argv[1:])
