"""Build JUnit classes into a Native Image and run them, as `mx native-unittest` does.

Arguments: the Native Image launcher script, the classpath manifest written by
svm_native_unittest, optionally `--default-group`, then `--tests` followed by test
classes and optionally `--build-args` followed by additional image builder arguments.

The selected classes are grouped by their effective @NativeImageBuildArgs with the
JUNIT_SUPPORT helper. Each group becomes one `svmjunit` image, which then runs its
classes with `--run-explicit`.
"""

import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

# The test features, providers and exports that mx_substratevm adds when the native unit
# tests include the SubstrateVM test suite (_native_unittest_features and
# _compute_native_unittest_args).
SVM_TEST_FEATURES = (
    "com.oracle.svm.test.ImageInfoTest$TestFeature",
    "com.oracle.svm.test.services.ServiceLoaderTest$TestFeature",
    "com.oracle.svm.test.services.SecurityServiceTest$TestFeature",
    "com.oracle.svm.test.ReflectionRegistrationTest$TestFeature",
    "com.oracle.svm.test.foreign.ForeignTests$TestFeature",
    "com.oracle.svm.test.BootstrapMethodTest$TestFeature",
)
SVM_TEST_OPTIONS = (
    "--add-exports=org.graalvm.nativeimage.builder/com.oracle.svm.core.jdk=ALL-UNNAMED",
    "--add-exports=org.graalvm.nativeimage.builder/com.oracle.svm.core.libjvm=ALL-UNNAMED",
    "--add-exports=org.graalvm.nativeimage.builder/com.oracle.svm.core.properties=ALL-UNNAMED",
    "--add-opens=org.graalvm.nativeimage.builder/com.oracle.svm.core.jdk=ALL-UNNAMED",
    "-H:AdditionalSecurityProviders=com.oracle.svm.test.services.SecurityServiceTest$NoOpProvider,sun.security.pkcs11.SunPKCS11",
    "-H:AdditionalSecurityServiceTypes=com.oracle.svm.test.services.SecurityServiceTest$JCACompliantNoOpService",
)
# IMAGE_ASSERTION_FLAGS, which the mx gate uses for its native unit tests.
IMAGE_ASSERTIONS = ("-H:+VerifyGraalGraphs", "-H:+VerifyPhases")


def experimental(options):
    return ["-H:+UnlockExperimentalVMOptions", *options, "-H:-UnlockExperimentalVMOptions"]


def write_services(root, service, implementations):
    directory = root / "META-INF/native-image-resource-test" / service
    directory.mkdir(parents=True, exist_ok=True)
    for name in implementations:
        (directory / name).write_text(name + "\n")


def resource_entries(work):
    """The extra classpath entries that _compute_native_unittest_args creates."""
    entry = work / "cpEntryDir"
    for index in range(4):
        conditional = entry / "resourcesFromDir" / f"cond-resource{index}.txt"
        simple = entry / "simpleDir" / f"simple-resource{index}.txt"
        for path, text in ((conditional, "Conditional file"), (simple, "Simple file")):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"{text}{index}\n")
    first = work / "micronautStyleServicesA"
    second = work / "micronautStyleServicesB"
    write_services(first, "com.oracle.svm.test.ServiceA", ["com.oracle.svm.test.impl.ServiceAImpl1", "com.oracle.svm.test.impl.ServiceAImplShared"])
    write_services(first, "com.oracle.svm.test.ServiceB", ["com.oracle.svm.test.impl.ServiceBImpl"])
    write_services(second, "com.oracle.svm.test.ServiceA", ["com.oracle.svm.test.impl.ServiceAImpl2", "com.oracle.svm.test.impl.ServiceAImplShared"])
    write_services(second, "com.oracle.svm.test.ServiceC", ["com.oracle.svm.test.impl.ServiceCImpl"])
    return [entry, first, second]


def split_arguments(args):
    groups = {"--tests": [], "--build-args": []}
    current = None
    for arg in args:
        if arg == "--default-group" and current is None:
            continue
        if arg in groups:
            current = arg
        elif current is None:
            raise SystemExit(f"Unexpected argument: {arg}")
        else:
            groups[current].append(arg)
    return groups["--tests"], groups["--build-args"]


def run(command, **kwargs):
    print("+ " + " ".join(map(shlex.quote, map(str, command))), flush=True)
    return subprocess.run(command, **kwargs)


def main(launcher, manifest_path, *args):
    launcher = Path(launcher).absolute()
    runfiles = Path(os.environ.get("TEST_SRCDIR") or os.environ["RUNFILES_DIR"])
    manifest = json.loads(Path(manifest_path).read_text())
    tests, extra_build_args = split_arguments(args)
    if not tests:
        raise SystemExit("Select at least one test class with --tests")
    work = Path(os.environ.get("TEST_TMPDIR") or os.getcwd()).absolute()

    def resolve(paths):
        return os.pathsep.join(str(runfiles / path) for path in paths)

    selected = work / "svmjunit.tests"
    selected.write_text("\n".join(tests) + "\n")
    groups_file = work / "svmjunit.tests.build-args-groups.json"
    run(
        [
            runfiles / manifest["java"],
            "--enable-preview",
            "-cp",
            resolve(manifest["helper_classpath"]),
            "com.oracle.svm.junit.NativeImageBuildArgsSupport",
            selected,
            groups_file,
        ],
        check=True,
    )
    groups = json.loads(groups_file.read_text())
    if "--default-group" in args[: args.index("--tests")]:
        skipped = [test for group in groups if group["buildArgs"] for test in group["tests"]]
        if skipped:
            print("Skipping classes with custom @NativeImageBuildArgs: " + " ".join(skipped), flush=True)
        groups = [group for group in groups if not group["buildArgs"]]
    entries = resource_entries(work)
    build_args = [
        "-ea",
        "-esa",
        "--add-exports=java.base/jdk.internal.misc=ALL-UNNAMED",
        "--add-exports=java.base/jdk.internal.vm=ALL-UNNAMED",
        "--features=" + ",".join(SVM_TEST_FEATURES),
        *experimental(["-cp", os.pathsep.join(map(str, entries))]),
        *experimental(SVM_TEST_OPTIONS),
        *experimental(IMAGE_ASSERTIONS),
        *extra_build_args,
    ]
    failures = []
    for index, group in enumerate(groups):
        directory = work / f"group-{index + 1}"
        directory.mkdir()
        tests_file = directory / "svmjunit.tests"
        tests_file.write_text("\n".join(group["tests"]) + "\n")
        print(f"Building a JUnit image with {group['buildArgs'] or 'no extra build arguments'} for: {' '.join(group['tests'])}", flush=True)
        run(
            [
                sys.executable,
                launcher,
                *build_args,
                *group["buildArgs"],
                "-cp",
                resolve(manifest["classpath"]),
                "--macro:junit=" + str(tests_file),
                *experimental(["-H:Path=" + str(directory)]),
            ],
            check=True,
            cwd=directory,
        )
        command = [directory / "svmjunit", "--verbose"]
        for test in group["tests"]:
            command += ["--run-explicit", test]
        # mx runs the image with JAVA_HOME set to the JDK it built with, and JDK tools on the
        # PATH of the CI hosts: JCmdTest runs $JAVA_HOME/bin/jcmd and JmxTest runs keytool.
        java_home = (runfiles / manifest["java"]).parents[1]
        environment = {
            **os.environ,
            "JAVA_HOME": str(java_home),
            "PATH": os.pathsep.join([str(java_home / "bin"), os.environ.get("PATH", "")]),
        }
        if run(command, cwd=directory, env=environment).returncode:
            failures.append(group["tests"])
    if failures:
        raise SystemExit("Failed image test runs: " + "; ".join(" ".join(tests) for tests in failures))


if __name__ == "__main__":
    main(*sys.argv[1:])
