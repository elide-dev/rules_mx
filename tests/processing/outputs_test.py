"""Check javac's full processed jar, including outputs from both processors."""

import sys
from pathlib import Path
from zipfile import ZipFile

jar = Path(__file__).parents[2] / sys.argv[1]
with ZipFile(jar) as archive:
    assert "app/Generated.class" in archive.namelist()
    assert archive.read("META-INF/processed.txt") == b"processed\n"
    assert archive.read("config.properties") == b"processor.fixture=true\n"
    assert "proc/Generator.class" not in archive.namelist()
