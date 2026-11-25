"""Compare the upstream SL launcher's output with one upstream fixture in both interpreters."""

from pathlib import Path
import subprocess
import sys


def main(launcher, source, expected, stdin=None):
    output = Path(expected).read_text()
    for bytecode in ("false", "true"):
        result = subprocess.run(
            [
                Path(launcher).absolute(),
                "--disable-launcher-output",
                "--sl.UseBytecode=" + bytecode,
                source,
            ],
            input=Path(stdin).read_text() if stdin else "",
            capture_output=True,
            text=True,
            check=False,
        )
        # SLTestRunner appends a guest exception's message to prior program output.
        # SLMain writes the same diagnostic to stderr and exits with status 1.
        code = 1 if result.stderr else 0
        if result.returncode != code or result.stdout + result.stderr != output:
            raise AssertionError(
                f"{source} with sl.UseBytecode={bytecode}: expected {output!r}, "
                f"got exit {result.returncode}, stdout {result.stdout!r}, stderr {result.stderr!r}"
            )


if __name__ == "__main__":
    main(*sys.argv[1:])
