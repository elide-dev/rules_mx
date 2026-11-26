"""Compile a patch for a JDK module against the matching execution JDK."""

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--java", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--module", required=True)
    parser.add_argument("--source-root", type=Path, action="append", required=True)
    parser.add_argument("--patch-jar", action="append", default=[])
    parser.add_argument("--javac-opt", action="append", default=[])
    parser.add_argument("sources", type=Path, nargs="+")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as work:
        # javac canonicalizes source symlinks. Bazel's local sandbox symlinks
        # individual files, which would place them outside --patch-module.
        source_root = Path(work) / "src"
        classes = Path(work) / "classes"
        classes.mkdir()
        sources = []
        for src in args.sources:
            root = next(
                (root for root in args.source_root if src.is_relative_to(root)), None
            )
            if root is None:
                parser.error(f"{src} is outside the declared source roots")
            staged = source_root / src.relative_to(root)
            if staged.exists():
                parser.error(f"duplicate module patch source {src.relative_to(root)}")
            staged.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, staged)
            sources.append(staged)
        subprocess.run(
            [
                args.java,
                "-m",
                "jdk.compiler/com.sun.tools.javac.Main",
                "-proc:none",
                "--patch-module",
                args.module
                + "="
                + os.pathsep.join([str(source_root), *args.patch_jar]),
                "-d",
                classes,
                *args.javac_opt,
                *sources,
            ],
            check=True,
        )
        with ZipFile(args.output, "w") as jar:
            for cls in sorted(classes.rglob("*.class")):
                info = ZipInfo(cls.relative_to(classes).as_posix())
                jar.writestr(info, cls.read_bytes(), compress_type=ZIP_DEFLATED)


if __name__ == "__main__":
    main()
