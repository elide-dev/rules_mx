"""Run the upstream SL launcher on the resolved Bazel module path."""

import os
from pathlib import Path
import sys


if __name__ == "__main__":
    # The generated entry point lives one suite directory below the Graal repository.
    root = Path(__file__).absolute().parents[2]
    java = str(root / {JAVA})
    compiler = {COMPILER}
    modules = os.pathsep.join(str(root / jar) for jar in {MODULES} if jar != compiler)
    options = [
        "--enable-native-access=org.graalvm.truffle",
        "-Dpolyglot.engine.WarnInterpreterOnly=" + ("true" if compiler else "false"),
        "-Dpolyglot.engine.AllowExperimentalOptions=true",
    ]
    if {JAVA_VERSION} >= 23:
        options.append("--sun-misc-unsafe-memory-access=allow")
    if tmp := os.environ.get("TEST_TMPDIR"):
        options.extend(["-Dpolyglot.engine.userResourceCache=" + tmp,
                        "-Dpolyglotimpl.AttachLibraryFailureAction=throw"])
    elif cwd := os.environ.get("BUILD_WORKING_DIRECTORY"):
        os.chdir(cwd)
    hsdis = {HSDIS}
    test_class = os.environ.get("MX_TEST_CLASS") if hsdis else {TEST_CLASS}
    if hsdis and not test_class:
        sys.exit("Run the compiler JUnit class by its Bazel test label.")
    probe = {PROBE}
    roots = ["org.graalvm.sl"]
    if compiler:
        options += ["-XX:+UnlockExperimentalVMOptions", "-XX:+EnableJVMCI", "-XX:-UseJVMCINativeLibrary",
                    "--upgrade-module-path", str(root / compiler)]
        roots += ["org.graalvm.truffle.runtime", "jdk.graal.compiler"]
    if probe:
        for package in ("org.graalvm.truffle.runtime/com.oracle.truffle.runtime",
                        "org.graalvm.truffle.runtime/com.oracle.truffle.runtime.hotspot",
                        "org.graalvm.sl/com.oracle.truffle.sl.runtime"):
            options.append("--add-exports=" + package + "=ALL-UNNAMED")
        entry = ["-cp", str(root / probe), "graal.CompiledGuest", str(root / compiler)]
    elif test_class:
        options.extend(["-ea", "-esa", "-Dbazel.test_suite=" + test_class,
                        "--add-exports=org.graalvm.truffle.compiler/com.oracle.truffle.compiler=ALL-UNNAMED",
                        "--add-exports=org.graalvm.truffle.runtime/com.oracle.truffle.runtime=ALL-UNNAMED"])
        options.extend({JVM_FLAGS})
        classpath = os.pathsep.join(str(root / jar) for jar in {CLASSPATH})
        main = "com.google.testing.junit.runner.BazelTestRunner"
        if hsdis:
            options.append("-Dtest.jdk.graal.compiler.disassembler.path=" + str(root / hsdis))
            main = "com.oracle.mxtool.junit.CompilerJUnit"
        entry = ["-cp", classpath, main]
    else:
        entry = ["-m", "org.graalvm.sl_launcher/com.oracle.truffle.sl.launcher.SLMain"]
    command = [java, *options, "--module-path", modules, "--add-modules", ",".join(roots), *entry, *sys.argv[1:]]
    if hsdis:
        command_file = Path(os.environ["TEST_TMPDIR"]) / "jvm-command"
        command_file.write_text("\n".join(command) + "\n")
        os.environ["MX_SUBPROCESS_COMMAND_FILE"] = str(command_file)
    os.execv(java, command)
