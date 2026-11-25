"""Run the two bridge processes in a writable directory for their Unix sockets."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile

if __name__ == "__main__":
    with tempfile.TemporaryDirectory(dir=os.environ.get("TEST_TMPDIR")) as workdir:
        subprocess.run(
            [str(Path(arg).absolute()) for arg in sys.argv[1:]],
            cwd=workdir,
            check=True,
            timeout=60,
        )
