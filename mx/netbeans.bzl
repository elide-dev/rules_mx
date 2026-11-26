"""Expose individual NetBeans Maven reactor modules as offline Bazel actions."""

load("@rules_java//java/common:java_common.bzl", "java_common")

_MavenModule = provider(fields = ["artifacts"])

def _module_impl(ctx):
    runtime = ctx.attr._jdk[java_common.JavaRuntimeInfo]
    coreutils = ctx.toolchains["@bazel_lib//lib:coreutils_toolchain_type"].coreutils_info.bin
    artifacts = {}
    for dep in ctx.attr.deps:
        artifacts.update(dep[_MavenModule].artifacts)
    group = ctx.attr.group.replace(".", "/") + "/"
    version = ctx.attr.version
    parent = ctx.file.parent
    parent_name = ctx.attr.parent_artifact
    artifacts[group + parent_name + "/" + version + "/" + parent_name + "-" + version + ".pom"] = parent
    base = group + ctx.attr.artifact + "/" + version + "/" + ctx.attr.artifact + "-" + version
    artifact = ctx.attr.artifact + "-" + version
    outputs = {}
    if ctx.attr.install:
        outputs["application"] = ctx.actions.declare_directory(ctx.label.name + ".files")
    else:
        suffixes = [".jar", ".nbm"] if ctx.attr.kind == "nbm" else [".jar"]
        if ctx.attr.test_jar:
            suffixes.append("-tests.jar")
        for ext in suffixes:
            outputs[ext] = ctx.actions.declare_file(ctx.label.name + "/" + artifact + ext)
    spec = ctx.actions.declare_file(ctx.label.name + ".json")
    ctx.actions.write(spec, json.encode({
        "artifact": ctx.attr.artifact,
        "coreutils": coreutils.path,
        "dependencies": {k: f.path for k, f in artifacts.items()},
        "install": ctx.attr.install,
        "java": runtime.java_executable_exec_path,
        "kind": ctx.attr.kind,
        "maven": ctx.file._maven_root.path,
        "module": ctx.attr.module,
        "outputs": {k: f.path for k, f in outputs.items()},
        "parent": parent.path,
        "repository": ctx.file._repo_root.path,
        "srcs": [f.path for f in ctx.files.srcs],
        "version": version,
    }))
    ctx.actions.run(
        executable = ctx.executable._runner,
        arguments = [spec.path],
        inputs = depset([parent, spec, coreutils, ctx.file._maven_root, ctx.file._repo_root] + ctx.files.srcs + artifacts.values(), transitive = [runtime.files, ctx.attr._maven.files, ctx.attr._repository.files]),
        outputs = outputs.values(),
        mnemonic = "NetBeansPackage",
        progress_message = "Packaging NetBeans module %{label}",
    )
    artifacts[base + ".pom"] = ctx.file.pom
    for ext, out in outputs.items():
        if ext != "application":
            artifacts[base + ext] = out
    return [DefaultInfo(files = depset(outputs.values())), _MavenModule(artifacts = artifacts)]

_module = rule(
    implementation = _module_impl,
    toolchains = ["@bazel_lib//lib:coreutils_toolchain_type"],
    attrs = {
        "artifact": attr.string(mandatory = True),
        "deps": attr.label_list(providers = [_MavenModule]),
        "group": attr.string(mandatory = True),
        "install": attr.string(),
        "kind": attr.string(mandatory = True),
        "module": attr.string(mandatory = True),
        "parent": attr.label(allow_single_file = True),
        "parent_artifact": attr.string(mandatory = True),
        "pom": attr.label(allow_single_file = True),
        "test_jar": attr.bool(),
        "srcs": attr.label_list(allow_files = True),
        "version": attr.string(mandatory = True),
        "_jdk": attr.label(default = Label("//tools:espresso_jdk21_runtime"), cfg = "exec"),
        "_maven": attr.label(default = Label("@netbeans_maven_distribution//:maven")),
        "_maven_root": attr.label(default = Label("@netbeans_maven_distribution//:BUILD.bazel"), allow_single_file = True),
        "_repository": attr.label(default = Label("@netbeans_maven_repository//:repository")),
        "_repo_root": attr.label(default = Label("@netbeans_maven_repository//:BUILD.bazel"), allow_single_file = True),
        "_runner": attr.label(default = Label("//mx:netbeans"), executable = True, cfg = "exec"),
    },
)

def mx_netbeans_project(name, root, group, parent_artifact, version, modules, install, visibility = None):
    """Build the reactor's module dependencies before assembling its NetBeans installation."""
    for directory, spec in modules.items():
        path = root + "/" + directory
        _module(
            name = name + "/" + directory,
            artifact = spec["artifact"],
            deps = [":" + name + "/" + d for d in spec["deps"]],
            group = group,
            install = install if spec["kind"] == "nbm-application" else "",
            kind = spec["kind"],
            module = directory,
            parent = root + "/pom.xml",
            parent_artifact = parent_artifact,
            pom = path + "/pom.xml",
            srcs = native.glob([path + "/**"], exclude = [path + "/target/**"]),
            test_jar = spec.get("test_jar", False),
            version = version,
            visibility = visibility,
        )
        if spec["kind"] == "nbm-application":
            native.alias(name = name, actual = ":" + name + "/" + directory, visibility = visibility)

def _archive_impl(ctx):
    out = ctx.actions.declare_file(ctx.label.name.lower() + ".tar")
    ctx.actions.run(
        executable = ctx.executable._pack,
        inputs = [ctx.file.installation],
        outputs = [out],
        arguments = ["--archive-file-tree", out.path, ctx.attr.prefix, ctx.file.installation.path],
        mnemonic = "MxNetBeansArchive",
    )
    return [DefaultInfo(files = depset([out]))]

mx_netbeans_distribution = rule(
    implementation = _archive_impl,
    attrs = {
        "installation": attr.label(allow_single_file = True, mandatory = True),
        "prefix": attr.string(mandatory = True),
        "_pack": attr.label(default = Label("//mx:pack_layout"), executable = True, cfg = "exec"),
    },
)

def _java_impl(ctx):
    runtime = ctx.attr.jdk[java_common.JavaRuntimeInfo]
    [java] = [f for f in runtime.files.to_list() if f.path == runtime.java_executable_exec_path]
    return [DefaultInfo(files = depset([java]), runfiles = ctx.runfiles(transitive_files = runtime.files))]

netbeans_test_java = rule(
    implementation = _java_impl,
    attrs = {"jdk": attr.label(mandatory = True, providers = [java_common.JavaRuntimeInfo])},
)
