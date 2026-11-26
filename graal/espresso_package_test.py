"""Check the distribution paths and release overlays consumed by Espresso."""

import io
import sys
import tarfile
from zipfile import ZipFile


def main(support, io_jar):
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


if __name__ == "__main__":
    main(*sys.argv[1:])
