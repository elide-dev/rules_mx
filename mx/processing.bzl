"""Native Java rules for compile-time annotation processing."""

load("@rules_java//java:java_library.bzl", _java_library = "java_library")
load("@rules_java//java:java_plugin.bzl", _java_plugin = "java_plugin")

java_library = _java_library
java_plugin = _java_plugin
