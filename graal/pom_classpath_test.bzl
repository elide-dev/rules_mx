"""Check the Java classpaths exported by a generated mx POM distribution."""

load("@rules_java//java/common:java_info.bzl", "JavaInfo")

def _test_impl(ctx):
    pom = ctx.attr.pom[JavaInfo]
    compile_jars = pom.transitive_compile_time_jars.to_list()
    runtime_jars = pom.transitive_runtime_jars.to_list()
    failures = []
    for target in ctx.attr.compile_deps + ctx.attr.runtime_only:
        info = target[JavaInfo]
        for output in info.java_outputs:
            if output.class_jar not in runtime_jars:
                failures.append("Missing runtime JAR " + output.class_jar.short_path)
            if (output.compile_jar in compile_jars) != (target in ctx.attr.compile_deps):
                failures.append("Incorrect compile scope for " + output.class_jar.short_path)
    return [AnalysisTestResultInfo(success = not failures, message = "\n".join(failures))]

pom_classpath_test = rule(
    implementation = _test_impl,
    analysis_test = True,
    attrs = {
        "pom": attr.label(mandatory = True, providers = [JavaInfo]),
        "compile_deps": attr.label_list(providers = [JavaInfo]),
        "runtime_only": attr.label_list(providers = [JavaInfo]),
    },
)
