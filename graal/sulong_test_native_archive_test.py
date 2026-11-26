"""Load the shared libraries installed by the mx Sulong native test distribution."""

import ctypes
import os
from pathlib import Path
import sys
import tarfile
import tempfile


def main(archive):
    extension = "dylib" if sys.platform == "darwin" else "so"
    with tempfile.TemporaryDirectory(dir=os.environ["TEST_TMPDIR"]) as directory:
        with tarfile.open(archive) as tar:
            files = {entry.name for entry in tar if entry.isfile()}
            assert files == {f"libpipe.{extension}", f"libsulongtest.{extension}"}, files
            tar.extractall(directory, filter="data")
        for library in files:
            ctypes.CDLL(str(Path(directory) / library))
        sulongtest = ctypes.CDLL(str(Path(directory) / f"libsulongtest.{extension}"))
        callback = ctypes.CFUNCTYPE(ctypes.c_long, ctypes.c_long)
        call = sulongtest.caller_i64
        call.argtypes = (callback, ctypes.c_long)
        call.restype = ctypes.c_long
        assert call(callback(lambda value: value + 1), 41) == 42


if __name__ == "__main__":
    main(sys.argv[1])
