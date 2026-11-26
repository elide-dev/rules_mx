"""Materialize the pinned Maven inputs for the two NetBeans application reactors."""

load("@bazel_tools//tools/build_defs/repo:http.bzl", "http_archive")

def _repository(ctx):
    lock = json.decode(ctx.read(ctx.attr.lock))
    items = lock["artifacts"].items()
    for i in range(0, len(items), 32):
        downloads = [
            ctx.download("https://repo.maven.apache.org/maven2/" + path, output = path, sha256 = digest, block = False)
            for path, digest in items[i:i + 32]
        ]
        for download in downloads:
            download.wait()
    for path, content in lock["metadata"].items():
        ctx.file(path, content, executable = False)
    ctx.file("BUILD.bazel", """package(default_visibility=["//visibility:public"])
exports_files(["BUILD.bazel"])
filegroup(name="repository", srcs=glob(["**/*.pom","**/*.jar","**/*.nbm","**/*.xml"]))
""")
    return ctx.repo_metadata(reproducible = True)

_netbeans_repo = repository_rule(
    implementation = _repository,
    attrs = {"lock": attr.label(mandatory = True)},
)

def _extension(ctx):
    _netbeans_repo(name = "netbeans_maven_repository", lock = Label("//graal:netbeans_maven.json"))
    http_archive(
        name = "netbeans_maven_distribution",
        urls = ["https://repo.maven.apache.org/maven2/org/apache/maven/apache-maven/3.9.16/apache-maven-3.9.16-bin.tar.gz"],
        sha256 = "80ffca22aed9e8b9713a232f3394fd81d7f20322df75efdb2b047dbd3e3a23bb",
        strip_prefix = "apache-maven-3.9.16",
        build_file_content = """package(default_visibility=["//visibility:public"])
exports_files(["BUILD.bazel"])
filegroup(name="maven", srcs=glob(["boot/**","lib/**","conf/**","bin/m2.conf"]))
""",
    )
    return ctx.extension_metadata(reproducible = True)

netbeans_maven = module_extension(implementation = _extension)
