load("@rules_mx//mx:launcher.bzl", "mx_launcher")
load("@rules_mx//mx:projects.bzl", "mx_java_projects")
load("@rules_python//python:py_binary.bzl", "py_binary")

package(default_visibility = ["//visibility:public"])

mx_launcher(
    name = "launcher",
    out = "mx_entry.py",
)

py_binary(
    name = "mx_cli",
    srcs = ["mx_entry.py"],
    data = glob([
        "src/**/*.py",
        "mx.mx/**",
        "ninja-toolchains/**",
        "src/**/*.c",
        "java/**",
        "eclipse-settings/**",
    ]) + [
        "common.json",
        "jdk-binaries.json",
        "mx",
        "mx.cmd",
        "mx.py",
        "pyproject.toml",
        ":launcher",
    ],
    legacy_create_init = False,
    main = "mx_entry.py",
)

mx_java_projects()

# The JDK 9 overlay replaces the Java 8 module-access implementation.
filegroup(
    name = "junit_runner_sources",
    srcs = glob(
        ["java/com.oracle.mxtool.junit/src/**/*.java"],
        exclude = ["**/ModuleSupport.java"],
    ) + glob([
        "java/com.oracle.mxtool.junit.jdk9/src/**/*.java",
    ]),
)
