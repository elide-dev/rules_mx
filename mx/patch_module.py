"""Compile a patch for a JDK module against the matching execution JDK."""

import argparse
import os
from pathlib import Path
import subprocess
import tempfile
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--java", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--module", required=True)
    parser.add_argument("--path", action="append", required=True)
    parser.add_argument("--javac-opt", action="append", default=[])
    parser.add_argument("sources", nargs="+")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as work:
        subprocess.run(
            [
                args.java,
                "-m",
                "jdk.compiler/com.sun.tools.javac.Main",
                "-proc:none",
                "--patch-module",
                args.module + "=" + os.pathsep.join(args.path),
                "-d",
                work,
                *args.javac_opt,
                *args.sources,
            ],
            check=True,
        )
        with ZipFile(args.output, "w") as jar:
            for cls in sorted(Path(work).rglob("*.class")):
                info = ZipInfo(cls.relative_to(work).as_posix())
                jar.writestr(info, cls.read_bytes(), compress_type=ZIP_DEFLATED)


if __name__ == "__main__":
    main()
