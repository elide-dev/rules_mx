"""Materialize the mx and Gazelle graph for live GraalVM checkouts."""

def _live_repository_impl(ctx):
    checkout_parent = ctx.getenv("RULES_MX_CHECKOUTS") or str(ctx.workspace_root.dirname)
    if not checkout_parent.startswith("/"):
        checkout_parent = str(ctx.workspace_root) + "/" + checkout_parent
    python = ctx.which("python3")
    bazel = ctx.which("bazel") or ctx.which("bazelisk")
    if not python or not bazel:
        fail("Live Graal checkout discovery requires python3 and Bazel on PATH")

    helper = ctx.path(ctx.attr._helper)
    for label in ctx.attr._graph_inputs + ctx.attr.patches:
        ctx.watch(label)
    patches = [ctx.path(label) for label in ctx.attr.patches]
    inventory = ctx.execute([python, helper, "scan", checkout_parent] + patches)
    if inventory.return_code:
        fail(inventory.stderr)
    inputs = json.decode(inventory.stdout)
    for file in inputs["watch_files"]:
        ctx.watch(file)
    for directory in inputs["watch_dirs"]:
        ctx.path(directory).readdir()

    # Bazel runs the generator's tool targets in a separate output base so they
    # can resolve and build while the main invocation is fetching @graal.
    repo = ctx.path("")
    tooling = repo.dirname.dirname.get_child("graal-graph")
    ctx.file(".graph-inputs.json", inventory.stdout, executable = False)
    ctx.report_progress("Generating BUILD files from live GraalVM checkouts")
    result = ctx.execute(
        [python, helper, "generate", repo, bazel, tooling] + patches,
        timeout = 1800,
    )
    if result.return_code:
        fail(result.stderr)
    ctx.delete(".graph-inputs.json")

graal_live_repository = repository_rule(
    implementation = _live_repository_impl,
    attrs = {
        "patches": attr.label_list(mandatory = True),
        "_helper": attr.label(default = Label("//tools:graal_live_repository.py")),
        "_graph_inputs": attr.label_list(default = [
            Label("//:MODULE.bazel"),
            Label("//graal:libffi.json"),
            Label("//graal:live_suites.json"),
            Label("//graal:packed_sources.json"),
            Label("//graal:sdk_llvm.json"),
            Label("//graal:sdk_musl.json"),
            Label("//graal:shaded_inputs.json"),
            Label("//graal:suite_imports.json"),
            Label("//graal:svm_inputs.json"),
            Label("//graal:visualvm.json"),
            Label("//mx:export.py"),
            Label("//mx:labsjdk.json"),
            Label("//mx:labsjdk21.json"),
            Label("//mx/gazelle:graph.go"),
            Label("//tools:generate-graal"),
        ]),
    },
)
