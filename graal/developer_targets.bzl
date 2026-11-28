"""Declare the public entry points for a GraalVM component."""

load("@graal//:live_suites.bzl", "LIVE_SUITES")

_PUBLIC = ["//visibility:public"]
_COMPONENTS = ["compiler", "sdk", "sulong", "truffle", "wasm"]
_LIVE_COMPONENTS = {"graal-js": "graaljs", "graalpython": "graalpy"}

def graal_component(build, tests, run = None, artifacts = {}, packaging_tests = {}, live_suite = None, manual = False):
    """Expose a component's daily targets and explicitly selected packages."""
    if live_suite and live_suite not in LIVE_SUITES:
        return

    tags = ["manual"] if manual else []
    native.filegroup(name = "build", srcs = build, tags = tags, visibility = _PUBLIC)
    native.test_suite(name = "test", tests = tests, tags = tags, visibility = _PUBLIC)
    if run:
        native.alias(name = "run", actual = run, tags = tags, visibility = _PUBLIC)
    for name, target in artifacts.items():
        native.alias(name = name, actual = target, tags = ["manual"], visibility = _PUBLIC)
    for name, targets in packaging_tests.items():
        native.test_suite(name = name, tests = targets, tags = ["manual"], visibility = _PUBLIC)

def graal_workspace_targets():
    """Collect the daily build products and tests in the shared workspace."""
    components = _COMPONENTS + [name for suite, name in _LIVE_COMPONENTS.items() if suite in LIVE_SUITES]
    native.filegroup(
        name = "build",
        srcs = ["//graal/" + name + ":build" for name in components],
        visibility = _PUBLIC,
    )
    native.test_suite(
        name = "test",
        tests = ["//graal/" + name + ":test" for name in components],
        visibility = _PUBLIC,
    )
