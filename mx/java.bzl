"""Resource paths for suite-root Java targets."""

load("@rules_elide//elide:defs.bzl", _elide_java_library = "elide_java_library")

def elide_java_library(**kwargs):
    """Compile an mx project and emit its ABI in the same unified request."""
    _elide_java_library(
        unified_outputs = select({
            Label("//mx:unified_jvm"): ["abi"],
            "//conditions:default": [],
        }),
        **kwargs
    )

def mx_resource_prefix(root, repo_relative = False):
    """Return a resource prefix for Elide (short_path) or rules_java (repo-relative)."""
    package = native.package_relative_label(":BUILD.bazel")
    parts = ["..", package.repo_name] if package.repo_name and not repo_relative else []
    if package.package:
        parts.append(package.package)
    return "/".join(parts + [root])
