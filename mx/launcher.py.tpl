"""Run upstream mx with the JDK selected during Bazel analysis."""

import os
from pathlib import Path
import runpy


if __name__ == "__main__":
    # The generated entry point lives at the root of the mx runfiles repository.
    java = Path(__file__).parent.parent / "{JAVA_KEY}"
    os.environ["JAVA_HOME"] = str(java.parent.parent)
    runpy.run_path(str(Path(__file__).with_name("mx.py")), run_name="__main__")
