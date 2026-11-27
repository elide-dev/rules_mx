"""Run one upstream Python test with GraalPy's source-built native zlib."""

import os
from pathlib import Path
import subprocess
import sys


RUN_TEST = """
import runpy
import sys
import zlib

assert hasattr(zlib, "Z_TREES"), zlib.ZLIB_RUNTIME_VERSION
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name="__main__")
"""


def main():
    launcher, script = (str(Path(arg).absolute()) for arg in sys.argv[1:3])
    command = [
        launcher,
        *sys.argv[4:],
        "--engine.WarnInterpreterOnly=false",
        "-B",
    ]
    work = Path(os.environ["TEST_TMPDIR"])
    env = dict(os.environ, XDG_CACHE_HOME=str(work), JAVA_TOOL_OPTIONS=f"-Duser.home={work}")
    subprocess.run(
        [*command, "-c", RUN_TEST, script, sys.argv[3]],
        cwd=work,
        env=env,
        check=True,
    )


if __name__ == "__main__":
    main()
