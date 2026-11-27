"""Exercise the relocated GraalPy JVM standalone with upstream library tests."""

import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile


PROBE = """
import _imp
import _sha3
import ctypes
import encodings
import os
import pyexpat
import sqlite3
import sys
import zlib

assert _imp.is_frozen("encodings")
assert sqlite3.connect(":memory:").execute("select 6 * 7").fetchone() == (42,)
assert _sha3.sha3_256(b"graalpy").digest_size == 32
assert pyexpat.ParserCreate().Parse("<graalpy/>", True) == 1
callback = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_int)(lambda value: value + 2)
assert callback(40) == 42
assert "Z_TREES" in vars(zlib), zlib.ZLIB_RUNTIME_VERSION
assert zlib.decompress(zlib.compress(b"graalpy")) == b"graalpy"
read_fd, write_fd = os.pipe()
try:
    assert os.write(write_fd, b"n") == 1
    assert os.read(read_fd, 1) == b"n"
finally:
    os.close(read_fd)
    os.close(write_fd)
assert os.path.samefile(sys.executable, sys.argv[1]), sys.executable
"""


def main():
    archive, test = (Path(path).absolute() for path in sys.argv[1:])
    with tempfile.TemporaryDirectory(prefix="graalpy home ") as temp:
        home = Path(temp)
        with tarfile.open(archive) as bundle:
            bundle.extractall(home, filter="data")
        (root,) = home.iterdir()
        launcher = root / "bin/graalpy"
        env = dict(os.environ, XDG_CACHE_HOME=temp, JAVA_TOOL_OPTIONS=f'-Duser.home="{temp}"')
        for arguments in (
            ["-c", PROBE, str(launcher)],
            [str(test), "ChecksumTestCase"],
        ):
            subprocess.run([launcher, "-B", *arguments], cwd=home, env=env, check=True)
        includes = subprocess.check_output(
            [root / "bin/graalpy-config", "--includes"], cwd=home, env=env, text=True
        )
        expected = root.resolve() / "include"
        if str(expected) not in includes:
            raise AssertionError(f"graalpy-config did not select the installed headers: {includes}")


if __name__ == "__main__":
    main()
