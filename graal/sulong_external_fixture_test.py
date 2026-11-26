"""Run a packaged NWCC reference executable and the matching LLVM bitcode."""

import os
from pathlib import Path
import subprocess
import sys
import tarfile


def main(archive, sdk, ref, bitcode):
    with tarfile.open(archive) as bundle:
        files = {m.name: m for m in bundle if m.isfile()}
        references = {p.removesuffix("/ref.out") for p in files if p.endswith("/ref.out")}
        variants = {p.removesuffix("/bitcode-O0.bc") for p in files if p.endswith("/bitcode-O0.bc")}
        assert references == variants and "nwcc_0.8.3/tests/hello.c.dir" in references
        for filename, source, argv in (
            ("ref.out", ref, []),
            ("bitcode-O0.bc", bitcode, [Path(sdk) / "bin/lli"]),
        ):
            item = files["nwcc_0.8.3/tests/hello.c.dir/" + filename]
            output = Path(os.environ["TEST_TMPDIR"]) / filename
            output.write_bytes(bundle.extractfile(item).read())
            assert output.read_bytes() == Path(source).read_bytes()
            output.chmod(item.mode)
            assert subprocess.check_output([*argv, output], timeout=15) == b"hello c engineers\n"


if __name__ == "__main__":
    main(*sys.argv[1:])
