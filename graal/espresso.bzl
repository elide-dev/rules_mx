"""Build the source-relocated Espresso module used by SubstrateVM."""

load("@rules_java//java/common:java_info.bzl", "JavaInfo")
load("@rules_mx//mx:graph.bzl", "mx_jar_dist", "mx_shaded_java")

def _source_jars_impl(ctx):
    return [DefaultInfo(files = depset([
        jar
        for project in ctx.attr.projects
        for jar in project[JavaInfo].source_jars
    ]))]

_source_jars = rule(
    implementation = _source_jars_impl,
    attrs = {"projects": attr.label_list(providers = [JavaInfo])},
)

def espresso_svm(name, project, shade, shade_inputs, module_name, directives):
    source_archive = project + ".source_jars"
    _source_jars(name = source_archive, projects = shade_inputs)
    mx_shaded_java(
        name = project,
        release = 21,
        shade = shade,
        shade_inputs = [":" + source_archive],
        javacopts = ["--release", "21", "-parameters"],
        deps = ["//sdk:COLLECTIONS"],
        visibility = ["//visibility:public"],
    )
    mx_jar_dist(
        name = name,
        module_name = module_name,
        contents = [":" + project],
        deps = ["//sdk:COLLECTIONS"],
        release = 21,
        directives = directives,
        visibility = ["//visibility:public"],
    )
