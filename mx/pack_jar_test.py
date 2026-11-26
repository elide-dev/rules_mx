"""Check mx archive participants and suite snippet filtering."""

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from zipfile import ZipFile

PACKER = Path(sys.argv[1]).absolute()


class PackJarTest(unittest.TestCase):
    def test_filters_project_snippets_and_merges_both_registration_kinds(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project.jar"
            library = project.with_name("vendor.jar")
            out = project.with_name("dist.jar")
            with ZipFile(project, "w") as jar:
                jar.writestr("p/Snippets.class", b"doc")
                jar.writestr("META-INF/truffle-registrations/p.Guest", b"p.Service")
                jar.writestr("META-INF/jni-entry-points/p.Bridge", b"p.Service")
            with ZipFile(library, "w") as jar:
                jar.writestr("other/Snippets.class", b"vendor")
            subprocess.run(
                [
                    PACKER,
                    "--input",
                    project,
                    "--input",
                    library,
                    "--archived-library",
                    library,
                    "--snippets-pattern",
                    ".*Snippets.*",
                    "--jni-services",
                    "--truffle-services",
                    "--output",
                    out,
                ],
                check=True,
            )
            with ZipFile(out) as jar:
                self.assertEqual(
                    set(jar.namelist()),
                    {"META-INF/services/p.Service", "other/Snippets.class"},
                )
                self.assertEqual(
                    jar.read("META-INF/services/p.Service"), b"p.Bridge\np.Guest\n"
                )

    def test_mxkeep_becomes_a_directory_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with ZipFile(root / "project.jar", "w") as jar:
                jar.writestr("resources/empty/.mxkeep", b"keep")
                jar.writestr("resources/file.txt", b"text")
            subprocess.run(
                [PACKER, "--input", root / "project.jar", "--output", root / "out.jar"],
                check=True,
            )
            with ZipFile(root / "out.jar") as jar:
                self.assertEqual(
                    jar.namelist(), ["resources/empty/", "resources/file.txt"]
                )
                self.assertTrue(jar.getinfo("resources/empty/").is_dir())

    def test_jmh_participant_combines_registry_and_hints(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            command = [PACKER, "--output", root / "out.jar", "--jmh-metadata"]
            for project in ("first", "second"):
                jar = root / (project + ".jar")
                with ZipFile(jar, "w") as archive:
                    archive.writestr("META-INF/BenchmarkList", project + "\n")
                    archive.writestr(
                        "META-INF/CompilerHints", "inline " + project + "\n"
                    )
                command.extend(["--input", jar])
            subprocess.run(command, check=True)
            with ZipFile(root / "out.jar") as result:
                self.assertEqual(
                    result.read("META-INF/BenchmarkList"), b"first\nsecond\n"
                )
                self.assertEqual(
                    result.read("META-INF/CompilerHints"),
                    b"inline first\ninline second\n",
                )

    def test_compiler_test_archive_consumes_unpublished_providers(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "classes.jar"
            out = project.with_name("test.jar")
            with ZipFile(project, "w") as jar:
                jar.writestr("META-INF/providers/p.CompilerPlugin", b"p.Plugin")
                jar.writestr(
                    "META-INF/versions/24/META-INF/providers/p.Versioned", b"p.Plugin"
                )
                jar.writestr("p/Test_OptionDescriptors.class", b"options")
                jar.writestr("META-INF/truffle-registrations/p.Language", b"p.Provider")
            subprocess.run(
                [
                    PACKER,
                    "--graal-test-services",
                    "--truffle-services",
                    "--input",
                    project,
                    "--output",
                    out,
                ],
                check=True,
            )
            with ZipFile(out) as jar:
                self.assertEqual(
                    set(jar.namelist()),
                    {"p/Test_OptionDescriptors.class", "META-INF/services/p.Provider"},
                )
                self.assertEqual(
                    jar.read("META-INF/services/p.Provider"), b"p.Language\n"
                )

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
