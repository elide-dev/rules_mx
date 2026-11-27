"""Run the upstream GraalPy freezer and package its bytecode as Java resources."""

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from zipfile import ZIP_DEFLATED, ZipFile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("java", "classpath", "launcher", "script", "stdlib", "index", "output"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    package = "com/oracle/graal/python/builtins/objects/module"
    with tempfile.TemporaryDirectory() as temp:
        directory = Path(temp)
        index = Path(args.index)
        shutil.copyfile(index, directory / index.name)
        frozen = directory / "frozen"
        subprocess.run(
            [
                args.java,
                "-Duser.home=" + temp,
                "-cp",
                args.classpath,
                args.launcher,
                "--engine.WarnInterpreterOnly=false",
                "--experimental-options",
                "--python.DisableFrozenModules=true",
                "--python.PosixModuleBackend=java",
                "--python.Sha3ModuleBackend=java",
                "--python.CompressionModulesBackend=java",
                "--python.PyExpatModuleBackend=java",
                "--python.UnicodeCharacterDatabaseNativeFallback=false",
                "-B",
                "-S",
                args.script,
                "--python-lib",
                str(Path(args.stdlib).parent),
                "--binary-dir",
                str(frozen),
                "--sources-dir",
                temp,
            ],
            env=dict(os.environ, XDG_CACHE_HOME=temp, PYTHONHASHSEED="0"),
            check=True,
        )
        with ZipFile(args.output, "w", compression=ZIP_DEFLATED) as jar:
            for file in sorted(frozen.glob("*.bin_dsl")):
                jar.writestr(f"{package}/{file.name}", file.read_bytes())


if __name__ == "__main__":
    main()
