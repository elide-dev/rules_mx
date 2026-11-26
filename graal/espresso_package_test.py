"""Check the distribution paths and release overlays consumed by Espresso."""

import hashlib
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


def main(
    support, io_jar, launchers, benchmarks, mokapot, platform_support, nm, resources
):
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

    with ZipFile(resources) as jar:
        prefixes = [
            n.removesuffix("sha256")
            for n in jar.namelist()
            if n.startswith("META-INF/resources/java/espresso-libs/")
            and n.endswith("/sha256")
        ]
        assert len(prefixes) == 1, prefixes
        prefix = prefixes[0]
        digest = hashlib.sha256()
        files = {}
        for line in jar.read(prefix + "files").decode().splitlines():
            member, mode = line.split(" = ")
            assert member.startswith(prefix) and member not in files
            files[member.removeprefix(prefix)] = mode
            digest.update(jar.read(member))
            digest.update(member.encode())
        assert digest.hexdigest() == jar.read(prefix + "sha256").decode()
        assert set(files) == {
            n.removeprefix(prefix) for n in jar.namelist() if n.startswith(prefix)
        } - {"files", "sha256"}
        assert files["lib/vmaccess-guest.jar"] == "rw-r--r--"
        assert files["lib/espresso-io.src.zip"] == "rw-r--r--"
        with ZipFile(
            io.BytesIO(jar.read(prefix + "lib/espresso-io.src.zip"))
        ) as sources:
            for version in (21, 25):
                assert any(
                    n.startswith(f"META-INF/versions/{version}/")
                    and n.endswith(".java")
                    for n in sources.namelist()
                )
        suffix = "dylib" if sys.platform == "darwin" else "so"
        assert files["lib/libnespresso." + suffix] == "rwxr-xr-x"
        assert files["lib/libjvm." + suffix] == "rwxr-xr-x"


if __name__ == "__main__":
    main(*sys.argv[1:])
