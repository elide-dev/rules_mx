load("@rules_mx//mx:java.bzl", "elide_java_library")
load("@rules_mx//mx:processing.bzl", "java_library", "java_plugin")
load("@rules_mx//mx:java.bzl", "mx_resource_prefix")

def mx_java_projects():
    java_library(
        name = "app",
        srcs = ["app/src/app/Api.java"],
        plugins = [
            ":mx_plugin.PROCESSORS.proc.Generator",
            ":mx_plugin.PROCESSORS.proc.ManifestProcessor",
        ],
        javacopts = [
            "--release",
            "17",
        ],
        resources = ["app/src/config.properties"],
        resource_strip_prefix = mx_resource_prefix("app/src", repo_relative = True),
    )

    elide_java_library(
        name = "consumer",
        srcs = ["consumer/src/end/Consumer.java"],
        javac_opts = [
            "--release",
            "17",
        ],
        unified_release = 17,
        deps = [":app"],
    )

    java_plugin(
        name = "mx_plugin.PROCESSORS.proc.Generator",
        processor_class = "proc.Generator",
        generates_api = True,
        deps = [":proc"],
    )

    java_plugin(
        name = "mx_plugin.PROCESSORS.proc.ManifestProcessor",
        processor_class = "proc.ManifestProcessor",
        generates_api = True,
        deps = [":proc"],
    )

    elide_java_library(
        name = "proc",
        srcs = [
            "proc/src/proc/Generator.java",
            "proc/src/proc/ManifestProcessor.java",
        ],
        javac_opts = [
            "--release",
            "17",
        ],
        unified_release = 17,
        resources = ["proc/src/META-INF/services/javax.annotation.processing.Processor"],
        resource_strip_prefix = mx_resource_prefix("proc/src"),
    )
