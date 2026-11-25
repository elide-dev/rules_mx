"""Run the upstream SL launcher on the resolved Bazel module path."""

import os
import sys

from python.runfiles import runfiles


if __name__ == "__main__":
    files = runfiles.Create()
    java = os.path.abspath(files.Rlocation({JAVA}))
    modules = os.pathsep.join(os.path.abspath(files.Rlocation(jar)) for jar in {MODULES})
    options = [
        "--enable-native-access=org.graalvm.truffle",
        "-Dpolyglot.engine.WarnInterpreterOnly=false",
        "-Dpolyglot.engine.AllowExperimentalOptions=true",
    ]
    if {JAVA_VERSION} >= 23:
        options.append("--sun-misc-unsafe-memory-access=allow")
    if tmp := os.environ.get("TEST_TMPDIR"):
        options.extend(["-Dpolyglot.engine.userResourceCache=" + tmp,
                        "-Dpolyglotimpl.AttachLibraryFailureAction=throw"])
    elif cwd := os.environ.get("BUILD_WORKING_DIRECTORY"):
        os.chdir(cwd)
    os.execv(java, [java, *options, "--module-path", modules, "--add-modules", "org.graalvm.sl",
                    "-m", "org.graalvm.sl_launcher/com.oracle.truffle.sl.launcher.SLMain", *sys.argv[1:]])
