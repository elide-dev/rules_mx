"""Check the checked-in project graph against the pinned mx checkout."""

import json
import shutil
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

    def test_resolved_graph_includes_processor_and_archive_edges(self):
        result = self.run_mx("bazel-graph", "--imports", suite=self.processor_suite)
        graph = json.loads(result.stdout)
        self.assertEqual(graph["primary_suite"], "processing")
        self.assertIn("mx", graph["suites"])
        nodes = graph["suites"]["processing"]
        self.assertEqual(
            nodes["processing:app"]["edges"]["annotation processor dependency"],
            ["processing:PROCESSORS"],
        )
        self.assertEqual(
            nodes["processing:PROCESSORS"]["archive_members"], ["processing:proc"]
        )

    def test_resolved_graph_preserves_packed_source_owner_and_root_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            suite = Path(tmp).resolve() / "example"
            metadata = suite / "mx.example"
            metadata.mkdir(parents=True)
            (suite / ".mx_vcs_root").touch()
            sources = suite.parent / "packed"
            sources.mkdir()
            definition = {
                "name": "example",
                "mxversion": "7.89.0",
                "projects": {
                    "demo": {
                        "javaCompliance": "17+",
                        "sourceDirs": ["src", str(sources / "include")],
                        "buildDependencies": ["INPUTS"],
                    },
                },
                "libraries": {
                    "INPUTS": {
                        "packedResource": True,
                        "optional": True,
                        "preExtractedPath": str(sources),
                    },
                },
            }
            (metadata / "suite.py").write_text("suite = " + repr(definition))
            result = self.run_mx("bazel-graph", suite=suite)
            project = json.loads(result.stdout)["suites"]["example"]["example:demo"]
            self.assertEqual(
                project["source_inputs"],
                [
                    {"suite": "example", "path": "demo/src"},
                    {"library": "example:INPUTS", "path": "include"},
                ],
            )

    def test_whole_suite_emits_independent_projects_and_packs_nested_layouts(self):
        with tempfile.TemporaryDirectory() as directory:
            suite = Path(directory).resolve()
            (suite / ".mx_vcs_root").touch()
            metadata = suite / "mx.inventory"
            metadata.mkdir()
            (metadata / "suite.py").write_text(
                "suite = "
                + repr(
                    {
                        "name": "inventory",
                        "mxversion": "7.89.0",
                        "projects": {
                            name: {"javaCompliance": "17+", "sourceDirs": ["src"]}
                            for name in ("first", "second")
                        },
                        "distributions": {
                            "APP": {
                                "dependencies": ["first"],
                                "moduleInfo": {
                                    "name": "demo.inventory",
                                    "exports": ["first"],
                                    "requires": ["java.net.http"],
                                },
                            },
                            "BASE": {
                                "native": True,
                                "layout": {"docs/info": "file:info"},
                            },
                            "LAYER": {
                                "native": True,
                                "layout": {
                                    "share/": "extracted-dependency:BASE/docs/info",
                                    "archives/original.tar": "dependency:BASE",
                                },
                            },
                            "CUSTOM": {"native": True, "layout": {"link": "link:info"}},
                            "WRAPPER": {
                                "native": True,
                                "layout": {"inner.tar": "dependency:CUSTOM"},
                            },
                        },
                    }
                )
            )
            (suite / "info").write_text("info\n")
            for name in ("first", "second"):
                sources = suite / name / "src" / name
                sources.mkdir(parents=True)
                (sources / "Main.java").write_text(
                    f"package {name}; public class Main {{}}"
                )
            failed = self.run_mx(
                "bazel-buildfile", "--gazelle", "--all", check=False, suite=suite
            )
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn(
                "CUSTOM: unsupported TAR input", failed.stderr + failed.stdout
            )
            partial = json.loads(
                self.run_mx(
                    "bazel-buildfile",
                    "--gazelle",
                    "--all",
                    "--partial",
                    suite=suite,
                ).stdout
            )
            self.assertEqual(
                partial["suite_targets"]["distributions"], ["APP", "BASE", "LAYER"]
            )
            self.assertEqual(
                list(partial["blocked_roots"].values()),
                [["inventory:CUSTOM", "inventory:WRAPPER"]],
            )
            result = self.run_mx(
                "bazel-buildfile",
                "--gazelle",
                "--all",
                "--provided-target",
                "CUSTOM",
                suite=suite,
            )
            graph = json.loads(result.stdout)
            self.assertEqual(graph["suite_targets"]["projects"], ["first", "second"])
            self.assertEqual(
                graph["suite_targets"]["distributions"],
                ["APP", "BASE", "CUSTOM", "LAYER", "WRAPPER"],
            )
            self.assertEqual(
                [p["name"] for p in graph["projects"]], ["first", "second"]
            )
            self.assertEqual(graph["distributions"][0]["contents"], [":first"])
            self.assertEqual(graph["distributions"][0]["release"], 17)
            base, layer, wrapper = graph["tar_layouts"]
            self.assertEqual(wrapper["members"], {":CUSTOM": "inner.tar"})
            self.assertEqual(base["members"], {"info": "docs/info"})
            self.assertEqual(layer["members"], {":BASE": "archives/original.tar"})
            self.assertEqual(
                layer["archive_imports"], {":BASE": "docs/info=share/info"}
            )

    def test_import_generated_processor_bindings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / ".mx_vcs_root").touch()
            provider = root / "processing"
            shutil.copytree(self.processor_suite, provider)
            index = root / "bindings.json"
            self.run_mx(
                "bazel-buildfile",
                "--all",
                "--gazelle",
                "--bindings-out",
                str(index),
                suite=provider,
            )
            bindings = json.loads(index.read_text())
            self.assertIn("PROCESSORS", bindings["artifacts"])
            expected = bindings["processors"]["PROCESSORS"]
            self.assertEqual(len(expected), 2)
            consumer = root / "consumer"
            metadata = consumer / "mx.consumer"
            metadata.mkdir(parents=True)
            definition = {
                "name": "consumer",
                "mxversion": "7.89.0",
                "imports": {"suites": [{"name": "processing", "subdir": True}]},
                "projects": {
                    "client": {
                        "testProject": True,
                        "javaProperties": {
                            "processor.jar": "<path:processing:PROCESSORS>"
                        },
                        "sourceDirs": ["src"],
                        "javaCompliance": "17+",
                        "annotationProcessors": ["processing:PROCESSORS"],
                    }
                },
            }
            (metadata / "suite.py").write_text("suite = " + repr(definition))
            source = consumer / "client/src/example"
            source.mkdir(parents=True)
            (source / "Client.java").write_text(
                "package example; public class Client {}"
            )
            result = self.run_mx(
                "bazel-buildfile",
                "--all",
                "--gazelle",
                "--partial",
                "--bindings",
                f"//upstream={index}",
                suite=consumer,
            )
            graph = json.loads(result.stdout)
            self.assertEqual(graph["blocked_roots"], {})
            self.assertEqual(
                graph["projects"][0]["test_data"], ["//upstream:PROCESSORS"]
            )
            self.assertEqual(
                graph["projects"][0]["processors"],
                ["//upstream:" + p for p in expected],
            )

    def test_packaged_test_discovery(self):
        with tempfile.TemporaryDirectory() as directory:
            suite = Path(directory).resolve()
            (suite / ".mx_vcs_root").touch()
            metadata = suite / "mx.published"
            metadata.mkdir()
            (metadata / "suite.py").write_text(
                "suite = "
                + repr(
                    {
                        "name": "published",
                        "mxversion": "7.89.0",
                        "projects": {
                            "tck": {
                                "sourceDirs": ["src"],
                                "javaCompliance": "17+",
                                "testProject": False,
                            }
                        },
                    }
                )
            )
            pkg = suite / "tck/src/example"
            pkg.mkdir(parents=True)
            for name, decl in [
                ("Base", "abstract public class Base"),
                ("Concrete", "public class Concrete extends Base"),
            ]:
                (pkg / (name + ".java")).write_text(
                    f"package example;\n/* abstract class Concrete */\n@RunWith(Parameterized.class)\n{decl} {{}}\n"
                )
            args = (
                "bazel-buildfile",
                "--gazelle",
                "--test-runtime",
                "published:tck=:TCK_TESTS",
                "--test-runtime",
                "published:tck=:LANGUAGE_PROVIDER",
            )
            result = self.run_mx(
                *args,
                "--test-jvm-flag",
                "published:tck=-Dtck.language=sl",
                "tck",
                suite=suite,
            )
            (project,) = json.loads(result.stdout)["projects"]
            self.assertEqual(project["tests"], ["example.Concrete"])
            self.assertEqual(
                project["test_runtime"], [":TCK_TESTS", ":LANGUAGE_PROVIDER"]
            )
            self.assertEqual(project["test_jvm_flags"], ["-Dtck.language=sl"])
            self.assertFalse(project.get("testonly"))
            result = self.run_mx(
                "bazel-buildfile",
                "--gazelle",
                "--test-launcher",
                "published:tck=:runner",
                "--manual-test-project",
                "tck",
                "--release",
                "21",
                "--project-release",
                "tck=17",
                "tck",
                suite=suite,
            )
            (launched,) = json.loads(result.stdout)["projects"]
            self.assertEqual(launched["release"], 17)
            self.assertEqual(launched["tests"], ["example.Concrete"])
            self.assertEqual(launched["test_launcher"], ":runner")
            self.assertTrue(launched["manual_tests"])
            self.assertNotIn("test_runtime", launched)
            invalid = self.run_mx(
                *args,
                "--test-runtime",
                "published:typo=:TCK_TESTS",
                "tck",
                suite=suite,
                check=False,
            )
            self.assertNotEqual(invalid.returncode, 0)
            self.assertIn("published:typo", invalid.stderr + invalid.stdout)

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
