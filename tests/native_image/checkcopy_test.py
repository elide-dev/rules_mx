"""Exercise the mx checker built by GraalVM Native Image on the test platform."""

from pathlib import Path
import subprocess
import sys
import tempfile


binary = Path(sys.argv[1]).resolve()
# CheckCopyright detects the checkout before parsing --help.
with tempfile.TemporaryDirectory() as checkout:
    (Path(checkout) / ".git").mkdir()
    result = subprocess.run([binary, "--help"], cwd=checkout, capture_output=True, text=True)
if result.returncode or "--assumed-modified" not in result.stdout + result.stderr:
    raise AssertionError(result)
