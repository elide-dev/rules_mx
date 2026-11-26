"""Compare each generated reference and LLVM bitcode program with upstream's install."""

import os
from pathlib import Path
import subprocess
import sys


def run(*args):
    result = subprocess.run(args, capture_output=True, timeout=15, check=True)
    return result.stdout, result.stderr


if __name__ == "__main__":
    sdk, *outputs = sys.argv[1:]
    lli = Path(sdk) / "bin/lli"
    references = {
        p.parent.name: p
        for p in map(Path, os.environ["UPSTREAM_OUTPUTS"].split())
        if p.name == "ref.out"
    }
    if len(references) * 2 != len(outputs):
        raise ValueError(
            "Expected a native reference and a bitcode file for every upstream case"
        )
    for ref, bitcode in zip(
        map(Path, outputs[::2]), map(Path, outputs[1::2]), strict=True
    ):
        expected = run(references[ref.parent.name])
        for command in [(ref,), (lli, bitcode)]:
            actual = run(*command)
            if actual != expected:
                raise AssertionError(f"{command}: {actual!r} != {expected!r}")
