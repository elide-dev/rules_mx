"""Run an upstream GraalJS script with the source-built launcher."""

from pathlib import Path
import subprocess
import sys


if __name__ == "__main__":
    launcher, script = map(lambda arg: Path(arg).absolute(), sys.argv[1:3])
    subprocess.run([launcher, "--engine.WarnInterpreterOnly=false", script], check=True)
