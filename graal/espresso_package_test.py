"""Check the distribution paths and release overlays consumed by Espresso."""

import io
from pathlib import Path
import os
import subprocess
import sys
import tarfile
from zipfile import ZipFile


def defined_symbols(tool, library):
    output = subprocess.check_output(
        [tool, "--defined-only", "--extern-only", str(library)], text=True
    )
    return {
        line.split()[-1].removeprefix("_").partition("@")[0]
        for line in output.splitlines()
        if line.split()
    }


def main(support, io_jar, launchers, benchmarks, mokapot, platform_support, nm):
    with tarfile.open(support) as archive:
        jars = {member.name for member in archive if member.isfile()}
        assert jars == {"lib/espresso-graal.jar", "lib/espresso-graal.src.zip"}, jars
        for filename, extension in (
            ("lib/espresso-graal.jar", "class"),
            ("lib/espresso-graal.src.zip", "java"),
        ):
            with ZipFile(io.BytesIO(archive.extractfile(filename).read())) as jar:
                member = (
                    "com/oracle/truffle/espresso/graal/EspressoGraalRuntime."
                    + extension
                )
                assert member in jar.namelist(), (filename, member)
    with ZipFile(io_jar) as jar:
        members = set(jar.namelist())
        for release in (21, 25):
            prefix = f"META-INF/versions/{release}/sun/nio/ch/"
            assert any(
                name.startswith(prefix) and name.endswith(".class") for name in members
            ), prefix

    scripts = Path(launchers, "bin")
    suffix = ".cmd" if os.name == "nt" else ""
    assert {script.name for script in scripts.iterdir()} == {
        "java" + suffix,
        "javac" + suffix,
    }
    if os.name != "nt":
        assert all(os.access(script, os.X_OK) for script in scripts.iterdir())
    with ZipFile(benchmarks) as archive:
        names = ("DeltaBlue", "Fibonacci", "Richards", "Sieve")
        assert set(archive.namelist()) == {f"interpreter/{name}.jar" for name in names}
        for name in names:
            with ZipFile(io.BytesIO(archive.read(f"interpreter/{name}.jar"))) as jar:
                assert f"{name}.class" in jar.namelist()
                manifest = jar.read("META-INF/MANIFEST.MF").decode().splitlines()
                assert f"Main-Class: {name}" in manifest

    if sys.platform == "darwin":
        library = Path(mokapot, "fatpot", "libjvm.dylib")
        assert not list(Path(platform_support).iterdir())
    else:
        library = Path(mokapot, "libjvm.so")
        assert "eden_RTLD_DEFAULT" in defined_symbols(
            nm, Path(platform_support, "lib", "libeden.so")
        )
    symbols = defined_symbols(nm, library)
    assert {"JNI_CreateJavaVM", "JVM_IsStaticallyLinked"} <= symbols
    if sys.platform == "darwin":
        # A symbol from the statically linked JDK, beyond the JVM shim itself.
        assert "JIMAGE_Open" in symbols


if __name__ == "__main__":
    main(*sys.argv[1:])
