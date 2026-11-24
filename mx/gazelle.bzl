"""Gazelle runner for mx suites migrating Java packages to Bazel."""

load("@gazelle//:def.bzl", "gazelle", "gazelle_binary")

def mx_gazelle(name = "gazelle"):
    """Create a source-indexing Gazelle runner in the consuming workspace."""
    binary = name + "_java"
    gazelle_binary(
        name = binary,
        languages = [Label("@contrib_rules_jvm//java/gazelle")],
    )
    gazelle(name = name, gazelle = ":" + binary)
