"""Verify the output of the SDK toolchain's C and C++ probes."""

from pathlib import Path
import subprocess
import sys

for binary in sys.argv[1:]:
    output = subprocess.check_output(
        [Path(binary).absolute()], text=True, stderr=subprocess.STDOUT
    )
    if output.strip() != "Hello, World!":
        sys.exit(f"{binary} produced {output!r}")
