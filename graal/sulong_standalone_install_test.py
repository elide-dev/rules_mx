"""Execute guest bitcode with lli from the two installed Sulong release archives."""

import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile


def main(program, archives):
    fixture = Path(program).absolute()
    for archive in archives:
        with tempfile.TemporaryDirectory(dir=os.environ["TEST_TMPDIR"]) as directory:
            with tarfile.open(archive) as tar:
                tar.extractall(directory, filter="data")
            launcher, = Path(directory).glob("*/bin/lli")
            run = subprocess.run([launcher, fixture], cwd=directory, capture_output=True, text=True, timeout=120)
            assert run.returncode == 42, f"{launcher}: {run.returncode}\n{run.stdout}\n{run.stderr}"


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:])
