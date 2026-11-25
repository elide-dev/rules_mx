"""Run the source-built Native Image driver with the Bazel-selected JDK and C compiler."""

import os
from pathlib import Path
import sys


def main():
    runfiles = Path(__file__).absolute().parents[2]
    root = runfiles / {SVM_ROOT}
    java = root / "bin/java"
    compiler = runfiles / {CC}
    builder = root / "lib/svm/builder"
    upgrade = root / "lib/jvmci"
    modules = os.pathsep.join(str(path) for path in [builder, root / "lib/truffle", upgrade / "nativeimage.jar", upgrade / "nativeimage-libgraal.jar", upgrade / "collections.jar", upgrade / "word.jar"])
    if cwd := os.environ.get("BUILD_WORKING_DIRECTORY"):
        os.chdir(cwd)
    args = sys.argv[1:]
    if not any(arg in {"--version", "--help", "--help-extra"} for arg in args):
        args = [
            "-H:+UnlockExperimentalVMOptions",
            "-H:CCompilerPath=" + str(compiler),
            # Bazel selects the pinned compiler. The driver probes for GCC or Apple Clang.
            "-H:-CheckToolchain",
            "-H:-UnlockExperimentalVMOptions",
            *args,
        ]
    flags = (builder / "driver-exports").read_text().splitlines()
    os.execv(java, [
        str(java),
        *flags,
        "-Dnative-image.root=" + str(root),
        "--upgrade-module-path", os.pathsep.join(str(upgrade / jar) for jar in ["graal.jar", "graal-management.jar"]),
        "--module-path", modules,
        "--enable-native-access=org.graalvm.nativeimage.driver,org.graalvm.nativeimage.builder,org.graalvm.nativeimage",
        "--module", "org.graalvm.nativeimage.driver/com.oracle.svm.driver.NativeImage",
        *args,
    ])


if __name__ == "__main__":
    main()
