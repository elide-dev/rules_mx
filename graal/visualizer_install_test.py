"""Launch NetBeans from each installed mx distribution with the declared JDK."""

import os
from pathlib import Path
import subprocess
import sys
import tarfile


def main(java, archives):
    jdk = Path(java).absolute().parent.parent
    tmp = Path(os.environ["TEST_TMPDIR"])
    for archive in archives:
        with tarfile.open(archive) as bundle:
            roots = {Path(entry.name).parts[0] for entry in bundle}
            name, = roots
            assert all(entry.isfile() or entry.isdir() for entry in bundle)
            bundle.extractall(tmp, filter="data")
        root = tmp / name
        assert list((root / name / "modules").glob("*.jar"))
        user = tmp / (name + ".user")
        user.mkdir()
        proc = subprocess.run(
            [root / "bin" / name, "--jdkhome", jdk, "--userdir", user,
             "-J-Djava.awt.headless=true", "--nosplash", "--help"],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60,
        )
        # NetBeans exits with status 2 after printing help from its loaded modules.
        assert proc.returncode in (0, 2) and "Module reload options:" in proc.stdout, proc.stdout


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:])
