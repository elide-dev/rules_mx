"""Assemble the mx native test library directory from target-platform artifacts."""

from pathlib import Path
import shutil
import sys

directory = Path(sys.argv[1])
directory.mkdir(exist_ok=True)
for source in map(Path, sys.argv[2:]):
    shutil.copyfile(source, directory / source.name)
