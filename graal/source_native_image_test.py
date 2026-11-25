"""Build and execute a Java program with the source-built Native Image driver."""

import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory


def check_output(executable):
    result = subprocess.run(
        [executable.absolute()], check=True, capture_output=True, text=True
    )
    if result.stdout != "built from source\n":
        raise AssertionError(f"Unexpected image output: {result.stdout!r}")


def main(builder, guest=None):
    if guest is None:
        check_output(Path(builder))
        return
    with TemporaryDirectory(dir=os.environ.get("TEST_TMPDIR")) as tmp:
        output = Path(tmp) / "from-source"
        subprocess.run(
            [
                Path(builder).absolute(),
                "--parallelism=2",
                "-cp",
                Path(guest).absolute(),
                "-o",
                output,
                "graal.SourceNativeImageGuest",
            ],
            check=True,
            cwd=tmp,
        )
        check_output(output)


if __name__ == "__main__":
    main(*sys.argv[1:])
