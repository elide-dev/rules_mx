load("@rules_mx//mx:java.bzl", "elide_java_library")

def mx_java_projects():
    elide_java_library(
        name = "com.oracle.mxtool.checkcopy",
        srcs = ["java/com.oracle.mxtool.checkcopy/src/com/oracle/mxtool/checkcopy/CheckCopyright.java"],
        javac_opts = [
            "--release",
            "21",
        ],
        unified_release = 21,
    )

    elide_java_library(
        name = "com.oracle.mxtool.compilerserver",
        srcs = [
            "java/com.oracle.mxtool.compilerserver/src/com/oracle/mxtool/compilerserver/CompilerDaemon.java",
            "java/com.oracle.mxtool.compilerserver/src/com/oracle/mxtool/compilerserver/ECJDaemon.java",
            "java/com.oracle.mxtool.compilerserver/src/com/oracle/mxtool/compilerserver/JavacDaemon.java",
        ],
        javac_opts = [
            "--release",
            "21",
        ],
        unified_release = 21,
    )

    elide_java_library(
        name = "com.oracle.mxtool.webserver",
        srcs = ["java/com.oracle.mxtool.webserver/src/com/oracle/mxtool/webserver/WebServer.java"],
        javac_opts = [
            "--release",
            "21",
        ],
        unified_release = 21,
    )
