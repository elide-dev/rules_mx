"""Check the checked-in project graph against the pinned mx checkout."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class ExportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        files = Path(__file__).parents[1]
        cls.mx = (files / sys.argv[1]).absolute()
        cls.snapshot = files / sys.argv[2]
        cls.processor_suite = (files / sys.argv[3]).parent

    def run_mx(self, *args, check=True, suite=None):
        result = subprocess.run(
            [self.mx, "-p", str(suite or self.mx.parent.resolve()), *args],
            capture_output=True,
            text=True,
        )
        if check:
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        return result

    def test_mx_can_render_the_same_descriptor_for_two_releases(self):
        sys.path.insert(0, str(self.mx.parent / "src"))
        try:
            from mx._impl.mx_javamodules import JavaModuleDescriptor

            descriptor = JavaModuleDescriptor(
                "m",
                {"p": {"client"}},
                {},
                [],
                {"p.Service": {"p.Impl"}},
                jarpath="m.jar",
            )
            for _ in range(2):
                source = descriptor.as_module_info(False)
                self.assertIn("exports p to client;", source)
                self.assertIn("provides p.Service with p.Impl;", source)
        finally:
            sys.path.pop(0)

    def test_project_snapshot(self):
        names = [
            "com.oracle.mxtool.webserver",
            "com.oracle.mxtool.compilerserver",
            "com.oracle.mxtool.checkcopy",
        ]
        result = self.run_mx("bazel-buildfile", "--macro", "--release", "21", *names)
        self.assertEqual(self.snapshot.read_text(), result.stdout)

    def test_processor_graph(self):
        generated = self.run_mx(
            "bazel-buildfile", "--macro", "consumer", suite=self.processor_suite
        )
        self.assertEqual(
            (self.processor_suite / "projects.bzl").read_text(), generated.stdout
        )
        mapped = self.run_mx(
            "bazel-buildfile",
            "--processor-plugin",
            "processing:PROCESSORS=//third_party:plugin",
            "consumer",
            suite=self.processor_suite,
        )
        self.assertIn('plugins = ["//third_party:plugin"]', mapped.stdout)
        self.assertNotIn("java_plugin(", mapped.stdout)

    def test_gazelle_processor_graph(self):
        result = self.run_mx(
            "bazel-buildfile", "--gazelle", "consumer", suite=self.processor_suite
        )
        graph = json.loads(result.stdout)
        projects = {p["name"]: p for p in graph["projects"]}
        self.assertEqual(projects["consumer"]["deps"], [":app"])
        self.assertNotIn("sources", projects["consumer"])
        self.assertIn("sources", projects["app"])
        self.assertEqual(projects["proc"]["resource_root"], "proc/src")
        self.assertEqual(
            set(projects["app"]["processors"]),
            {":" + plugin["name"] for plugin in graph["plugins"]},
        )
        self.assertEqual(len(graph["plugins"]), 2)
        self.assertTrue(all(plugin["deps"] == [":proc"] for plugin in graph["plugins"]))

    def test_classpath_distribution_root(self):
        args = ("bazel-buildfile", "--classpath-dists")
        result = self.run_mx(
            *args, "--gazelle", "PROCESSORS", suite=self.processor_suite
        )
        graph = json.loads(result.stdout)
        self.assertEqual(
            graph["distributions"],
            [{"name": "PROCESSORS", "deps": [":proc"], "testonly": False}],
        )
        self.assertEqual([p["name"] for p in graph["projects"]], ["proc"])
        invalid = self.run_mx(
            *args, "PROCESSORS", suite=self.processor_suite, check=False
        )
        self.assertNotEqual(invalid.returncode, 0)
        self.assertIn("--classpath-dists requires --gazelle", invalid.stderr)

    def test_layout_with_undeclared_host_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            suite = Path(directory).resolve()
            (suite / ".mx_vcs_root").touch()
            metadata = suite / "mx.layout"
            metadata.mkdir()
            (metadata / "suite.py").write_text(
                "suite = "
                + repr(
                    {
                        "name": "layout",
                        "mxversion": "7.89.0",
                        "distributions": {
                            "FILES": {
                                "type": "dir",
                                "platformDependent": False,
                                "layout": {"value": "file:local-input.txt"},
                            }
                        },
                    }
                )
            )
            result = self.run_mx(
                "bazel-buildfile",
                "--gazelle",
                "--classpath-dists",
                "FILES",
                suite=suite,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(
                "unsupported resource layout source", result.stdout + result.stderr
            )

    def test_graph_preserves_compliance_range(self):
        result = self.run_mx("bazel-projects", "com.oracle.mxtool.webserver")
        self.assertTrue(result.stdout.startswith("{"), result.stdout)
        (project,) = json.loads(result.stdout)["projects"]
        self.assertEqual(project["compliance"], "8+")
        self.assertEqual(
            project["source_roots"], ["java/com.oracle.mxtool.webserver/src"]
        )

    def test_java_dependency_closure_and_resources(self):
        with tempfile.TemporaryDirectory() as directory:
            suite = Path(directory).resolve()
            (suite / ".mx_vcs_root").touch()
            projects = {}
            for name in ("api", "app"):
                source = suite / name / "src"
                source.mkdir(parents=True)
                (source / "Example.java").write_text(
                    f"package {name}; class Example {{}}"
                )
                projects[name] = {"sourceDirs": ["src"], "javaCompliance": "17+"}
            projects["app"]["dependencies"] = ["api"]
            (suite / "api/src/message.txt").write_text("mx")
            metadata = suite / "mx.example"
            metadata.mkdir()
            (metadata / "suite.py").write_text(
                "suite = "
                + repr({"name": "example", "mxversion": "7.89.0", "projects": projects})
            )
            result = self.run_mx("bazel-buildfile", "app", suite=suite)
            self.assertEqual(result.stdout.count("elide_java_library("), 2)
            self.assertIn('deps = [":api"]', result.stdout)
            self.assertIn('resources = ["api/src/message.txt"]', result.stdout)
            self.assertIn('mx_resource_prefix("api/src")', result.stdout)

    def test_unsupported_project_fails_before_printing_rules(self):
        result = self.run_mx("bazel-buildfile", "com.oracle.jvmtiasmagent", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("elide_java_library(", result.stdout)


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
