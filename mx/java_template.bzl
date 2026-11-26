"""Compile an mx Java source template with the project's annotation processors."""

load("@bazel_skylib//rules:expand_template.bzl", "expand_template")
load("//mx:graph.bzl", "mx_processed_java")

def mx_java_template(name, template, output, substitutions, **kwargs):
    expand_template(
        name = name + ".source",
        template = template,
        out = output,
        substitutions = substitutions,
        visibility = ["//visibility:private"],
    )
    mx_processed_java(name = name, srcs = [":" + name + ".source"], **kwargs)
