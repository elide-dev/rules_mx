"""Compare each generated reference and LLVM bitcode program with upstream's install."""

from pathlib import Path
import subprocess
import sys


def run(*args):
    result = subprocess.run(args, capture_output=True, timeout=15, check=True)
    return result.stdout, result.stderr


if __name__ == "__main__":
    sdk, case, ref, bitcode, *upstream = sys.argv[1:]
    (installed,) = [path for path in upstream if path.endswith("/" + case)]
    expected = run(installed)
    for command in [(ref,), (Path(sdk) / "bin/lli", bitcode)]:
        actual = run(*command)
        if actual != expected:
            raise AssertionError(f"{command}: {actual!r} != {expected!r}")
