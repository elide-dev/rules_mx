"""Compile and run a guest program with the installed Espresso launchers."""

import os
from pathlib import Path
import subprocess
import sys


def main(installation):
    root = Path(installation).resolve()
    work = Path(os.environ["TEST_TMPDIR"])
    source = work / "Guest.java"
    source.write_text(
        "class Guest { public static void main(String[] args) { "
        'System.out.println(System.getProperty("java.vm.name") + ":" + (6 * 7)); } }',
        encoding="utf-8",
    )
    assert not (root / "lib/server").exists()
    assert (root / "languages/java/jvm/lib/server").is_dir()
    subprocess.run([root / "bin/javac", "-d", work, source], check=True, cwd=work)
    result = subprocess.check_output(
        [root / "bin/java", "-cp", work, "Guest"], text=True, cwd=work
    )
    assert result.strip().endswith(":42") and "espresso" in result.lower(), result


if __name__ == "__main__":
    main(sys.argv[1])
