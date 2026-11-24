"""Exercise the mx checker built by GraalVM Native Image on the test platform."""

from pathlib import Path
import subprocess
import sys


binary = Path(sys.argv[1]).resolve()
result = subprocess.run([binary, "--help"], capture_output=True, text=True, check=True)
if "--assumed-modified" not in result.stdout + result.stderr:
    raise AssertionError(result)
