"""Execute guest bitcode with lli from the two installed Sulong release archives."""

import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile


def main(program, objdump, archives):
    fixture = Path(program).absolute()
    for archive in archives:
        with tempfile.TemporaryDirectory(dir=os.environ["TEST_TMPDIR"]) as directory:
            with tarfile.open(archive) as tar:
                tar.extractall(directory, filter="data")
            launcher, = Path(directory).glob("*/bin/lli")
            run = subprocess.run([launcher, fixture], cwd=directory, capture_output=True, text=True, timeout=120)
            if run.returncode != 42:
                if sys.platform.startswith("linux"):
                    install = launcher.parent.parent
                    for lib in sorted((install / "lib/sulong/native/lib").glob("*.so*")):
                        if not lib.is_symlink():
                            tags = subprocess.check_output([objdump, "-p", lib], text=True, errors="replace")
                            print(lib.name, [row.strip() for row in tags.splitlines() if any(t in row for t in ("NEEDED", "RUNPATH", "RPATH"))])
                    traced = subprocess.run([launcher, fixture], cwd=directory, capture_output=True, text=True,
                                            env=os.environ | {"LD_DEBUG": "libs,files"}, timeout=120)
                    for line in traced.stderr.splitlines():
                        if "libc++.so.1" in line and ("needed by" in line or "dynamically loaded by" in line):
                            print(line)
                raise AssertionError(f"{launcher}: {run.returncode}\n{run.stdout}\n{run.stderr}")


if __name__ == "__main__":
    main(sys.argv[1], str(Path(sys.argv[2]).absolute()), sys.argv[3:])
