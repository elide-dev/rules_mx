"""Source resources and native compression support for the GraalPy JVM launcher."""

load("@rules_cc//cc:cc_binary.bzl", "cc_binary")
load("//mx:graph.bzl", "mx_resource_layout")
load(":layout.bzl", "mx_archive_layout")

_RESOURCE_ROOT = "META-INF/resources/"

def _resource_layout(name, category, members, literals = {}):
    mx_archive_layout(
        name = name,
        format = "dir",
        members = members,
        literals = literals,
        file_list_entry = _RESOURCE_ROOT + category + ".files",
        hash_entry = _RESOURCE_ROOT + category + ".sha256",
        use_param_file = True,
    )

def graalpy_resources(version, abi, abiflags, graal_version, libraries, headers, zsupport_srcs, zsupport_copts):
    """Package the inputs read by GraalPy's upstream InternalResource provider."""
    resource_jars = []
    for category, files in libraries.items():
        name = "py_resource_" + category
        _resource_layout(name, category, {
            source: _RESOURCE_ROOT + category + "/" + path
            for source, path in files.items()
        })
        resource_jars.append(":" + name)

    _resource_layout("py_resource_headers", "include", {
        source: _RESOURCE_ROOT + "include/" + path
        for source, path in headers.items()
    })
    _resource_layout("py_resource_ni", "ni", {
        "mx.graalpython/native-image.properties": _RESOURCE_ROOT + "native-image.properties",
    })

    native_versions = {}
    native_libs = {}
    language_versions = {}
    for profile, os, arch, machine, extension in [
        ("linux_amd64", "linux", "amd64", "x86_64", ".so"),
        ("linux_arm64", "linux", "aarch64", "aarch64", ".so"),
        ("macos_arm64", "darwin", "aarch64", "aarch64", ".dylib"),
    ]:
        condition = Label("//tools/cc:profile_" + profile)
        platform = os + "/" + arch
        multiarch = machine + "-" + os
        soabi = abi + abiflags + "-native-" + multiarch
        version_contents = "\n".join([version, abiflags, soabi, "." + soabi + ".so", multiarch, ""])
        binary = "py_zsupport/" + profile + "/libzsupport" + extension
        cc_binary(
            name = binary,
            srcs = zsupport_srcs,
            copts = zsupport_copts,
            deps = [Label("@zlib//:zlib")],
            linkshared = True,
            linkstatic = True,
            target_compatible_with = select({condition: [], "//conditions:default": ["@platforms//:incompatible"]}),
        )
        manifest = "py_versions_" + profile
        _resource_layout(manifest, platform + "/versions", {}, {
            _RESOURCE_ROOT + platform + "/graalpy_versions": version_contents,
        })
        libs = "py_native_" + profile
        _resource_layout(libs, platform + "/native", {
            ":" + binary: _RESOURCE_ROOT + platform + "/lib/graalpy" + graal_version + "/libzsupport" + extension,
        })
        language_version = "py_version_" + profile
        mx_resource_layout(name = language_version, literals = {"graalpy_versions": version_contents})
        native_versions[condition] = ":" + manifest
        native_libs[condition] = ":" + libs
        language_versions[condition] = ":" + language_version

    native.alias(name = "py_version", actual = select(language_versions))
    native.alias(
        name = "py_native_resources",
        actual = select(native_libs),
    )
    # Expose JavaInfo for each resource JAR to the launcher.
    native.alias(
        name = "py_platform_versions",
        actual = select(native_versions),
    )
    return resource_jars + [
        ":py_version",
        ":py_platform_versions",
        ":py_native_resources",
        ":py_resource_headers",
        ":py_resource_ni",
    ]
