"""Check NativeBridgeArchiveParticipant semantics for multi-release input markers."""

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from zipfile import ZipFile

PACKER = Path(sys.argv[1]).absolute()


class PackJarTest(unittest.TestCase):
    def test_accumulates_versioned_providers_and_consumes_markers(self):
        with tempfile.TemporaryDirectory() as directory:
            jar = Path(directory) / "members.jar"
            service = "META-INF/services/p.Service"
            marker = "META-INF/jni-entry-points/"
            with ZipFile(jar, "w") as archive:
                for name, data in {
                    service: b"p.A\n# comment\n",
                    marker + "p.A": b"p.Service",
                    marker + "p.B": b"p.Service\np.Other",
                    "META-INF/versions/11/" + marker + "p.C": b"p.Service",
                    "META-INF/versions/17/" + marker + "p.D": b"p.Service",
                    "p/unchanged": b"bytes",
                }.items():
                    archive.writestr(name, data)
            out = jar.with_name("out.jar")
            cmd = [PACKER, "--jni-services", "--input", jar, "--output", out]
            subprocess.run(cmd, check=True)
            with ZipFile(out) as archive:
                result = {name: archive.read(name) for name in archive.namelist()}
            self.assertEqual(
                result,
                {
                    service: b"p.A\np.B\n",
                    "META-INF/services/p.Other": b"p.B\n",
                    "META-INF/_versions/11/" + service: b"p.A\np.B\np.C\n",
                    "META-INF/_versions/17/" + service: b"p.A\np.B\np.C\np.D\n",
                    "p/unchanged": b"bytes",
                },
            )
            original = out.read_bytes()
            subprocess.run(cmd, check=True)
            self.assertEqual(original, out.read_bytes())


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
