"""Emit the JVM entry points required by the pinned static JDK archives."""

import ast
from pathlib import Path
import re
import subprocess
import sys


def main(output, nm, implementation, metadata, *archives):
    undefined = subprocess.run(
        [nm, "--undefined-only", "--just-symbol-name", *archives],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    needed = set(re.findall(r"(?m)^_?(JVM_\w+)$", undefined))
    provided = set(
        re.findall(r"\bJNICALL\s+(JVM_\w+)\s*\(", Path(implementation).read_text())
    )
    if not needed or not provided:
        raise ValueError("Expected JVM entry points from both the JDK and SubstrateVM")
    no_env = set(
        ast.literal_eval(
            re.search(r"noJNIEnvParam\s*=\s*(\[[^\]]+\])", Path(metadata).read_text())[
                1
            ]
        )
    )
    with Path(output).open("w") as source:
        source.write("#include <jni.h>\n#include <stdio.h>\n#include <stdlib.h>\n")
        for name in sorted(needed - provided):
            if name in no_env:
                source.write(
                    f"JNIEXPORT void JNICALL {name}(void) {{\n"
                    f'    fputs("{name} called:  Unimplemented\\n", stderr);\n'
                    "    abort();\n}\n"
                )
            else:
                source.write(
                    f"JNIEXPORT jobject JNICALL {name}(JNIEnv *env) {{\n"
                    f'    (*env)->FatalError(env, "{name} called:  Unimplemented");\n'
                    "    return NULL;\n}\n"
                )


if __name__ == "__main__":
    main(*sys.argv[1:])
