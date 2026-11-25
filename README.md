# rules_mx

Build mx Java projects as individual Bazel targets. mx loads the suite and resolves its dependencies during migration. The generated declarations can then be reviewed, checked in, and built with Elide. Java compile actions consume source files, compile-time dependency jars and execution toolchains. They have no dependency on the mx launcher, suite.py or the discovery checkout.

## Try the pinned build

Use Bazelisk from the repository root. The module fetches the exact mx revision used to initialize this repository. It works independently of the location of a developer's mx checkout.

```sh
bazelisk run //:mx -- --version
bazelisk run --config=elide-python //:mx -- --version
bazelisk build //:webserver @graal_mx//:com.oracle.mxtool.checkcopy @graal_mx//:com.oracle.mxtool.compilerserver
tools/check-graph
bazelisk build //tests/gazelle/...
bazelisk test //mx:export_test
bazelisk test --config=elide-python //mx:export_test
```

The default Python is managed CPython 3.13. The alternate configuration selects the matching Elide Python runtime for macOS arm64 or Linux x86_64/arm64. Both paths use the same rules_python binary. The checked-in rules_elide archive override provides the overlay support. Elide also compiles the Java targets and supplies JavaInfo and ABI jars. The launcher supplies the file-backed JDK selected by Bazel to mx as JAVA_HOME. Use a downloaded Java runtime in a consuming root, for example with --java_runtime_version=remotejdk_21.

## Build upstream GraalVM

The pinned Oracle source tree builds every SDK project and the Truffle Polyglot implementation as native Bazel targets. Run the upstream SDK JUnit classes individually or build the selected closures and run their tests together.

```sh
bazelisk query 'tests(//graal:sdk_tests)'
bazelisk test --config=elide-processors //graal/... @graal//sdk/... @graal//truffle/...
bazelisk test --config=elide-processors //graal/nativebridge:native_isolate_test
```

The Native Bridge test builds a shared library and exercises the generated JNI bindings in the JVM process and through the C++ launcher. The migration notes cover the standalone benchmark, BuildBuddy commands and remaining suites. [GraalVM migration](graal/README.md)

## Import a suite

Run discovery against a trusted, prepared checkout. This is a developer operation. mx loads Python suite extensions and applies its normal environment, platform, JDK and suite-import selection. Invoke discovery separately for each relevant configuration when migrating conditional suites.

```sh
bazelisk run //:mx -- -p "$HOME/workspace/graal/sdk" bazel-projects org.graalvm.collections
bazelisk run //:mx -- -p "$HOME/workspace/graal/sdk" bazel-buildfile org.graalvm.collections
```

The first command prints versioned JSON with suite-relative roots, resolved dependency names, Java compliance ranges and processors. The second prints BUILD declarations for the selected projects and their supported same-suite dependencies. Place these declarations at the suite root in its Bazel workspace. They enumerate Java and resource inputs and preserve resource paths within jars. Choose the release explicitly with `--release N`, or use the declared lower bound. mx's own checkcopy source requires a newer API than its declared minimum, so the three bundled projects use release 21.

Regenerate the bundled macro and review changes with:

```sh
bazelisk run //:mx -- bazel-buildfile --macro --release 21 \
    com.oracle.mxtool.checkcopy com.oracle.mxtool.compilerserver \
    com.oracle.mxtool.webserver > /tmp/mx-projects.bzl &&
diff -u mx/projects.bzl /tmp/mx-projects.bzl
```

The exporter handles plain Java projects and a single resource strip root per project. It exports annotation processor distributions backed by Java projects. External Java libraries and previously migrated distributions can be bound with --java-dep SUITE:NAME=LABEL. It diagnoses custom project types and unhandled project attributes. Mapping those features is part of migrating the rest of GraalVM. The generated suite-root targets assume their files share one Bazel package. Introducing nested BUILD files changes ownership of the sources.

## Annotation processors

For each mx-declared processor distribution, the exporter uses mx's resolved service-provider list and emits one native `java_plugin` per processor class. A processed project becomes a rules_java `java_library` with explicit plugins. Bazel runs the processors on its execution platform and exposes generated API to downstream Java compilations. Processor implementation projects and ordinary consumers still use `elide_java_library`.

```sh
bazelisk run //:mx -- -p "$PWD/tests/processing" bazel-buildfile --macro consumer
bazelisk test --config=elide-processors //tests/processing:outputs_test //mx:export_test
```

The `elide-processors` profile selects the pinned Elide JavaBuilder adapter. It runs eligible compilations through Elide and delegates requests requiring native JNI headers to stock JavaBuilder. The default Java toolchain also builds these native declarations. For binary processors, cross-suite processor distributions, or distributions that require archive transformations, pass `--processor-plugin SUITE:DIST=//pkg:plugin`. Repeat the option for multiple plugin labels. `--java-dep` binds ordinary classpath dependencies to JavaInfo targets independently of the processor path.

## Generate finer Java targets

`mx_gazelle` bundles the Java extension from contrib_rules_jvm. In a consuming workspace, load it in the root BUILD file and configure Gazelle's rule mapping:

```starlark
load("@rules_mx//mx:gazelle.bzl", "mx_gazelle")

# gazelle:map_kind java_library elide_java_library @rules_mx//mx:java.bzl

mx_gazelle()
```

Run `bazelisk run //:gazelle -- path/to/java/tree` for a tree assigned to package-level generation. The checked-in fixture demonstrates two packages with a resolved import edge. Use project-level generation for mutually dependent packages that must be compiled together. For Maven imports, point the Java extension at the consuming workspace's Maven lockfile.

### Import mx project edges into Gazelle

Export a selected project closure once, then run Gazelle on that suite. Both paths below are relative to the Bazel repository root. Keep the graph next to the suite's root BUILD file.

```sh
bazelisk run //:mx -- -p "$PWD/tests/gazelle" bazel-buildfile --gazelle \
  --java-dep mx:JUNIT=@contrib_rules_jvm_deps//:junit_junit check > /tmp/demo-graph.json &&
  cp /tmp/demo-graph.json tests/gazelle/mx-graph.json
bazelisk run //:gazelle -- -mx_graph=tests/gazelle/mx-graph.json tests/gazelle
bazelisk query 'deps(//tests/gazelle:check.example.check.ConsumerTest, 3)'
bazelisk test //tests/gazelle:check.example.check.ConsumerTest
```

The Java extension resolves source imports. For whole-project compilations, `--infer-source-deps` promotes Java imports found on the resolved mx classpath to direct Bazel dependencies. Use `--generated-dep SUITE:PROJECT=LABEL` for dependencies referenced by generated Java. The mx importer preserves declared project dependencies and Java releases and exposes each project by name. Building that label produces its package jars, and Java consumers receive their combined compile and runtime classpaths. Plain Java projects use one library per source package using the configured Gazelle Java naming convention. Test projects, projects with resources, and annotation-processed projects keep one compilation unit. Each discovered JUnit 4 test class gets a runnable label. Add `--whole-project NAME` at export time for packages that must compile together. Run generation across the entire selected suite when the graph changes and review removed targets and any old nested BUILD files when changing granularity.

## Consume from another Bzlmod root

Declare `rules_mx` and use a `local_path_override` pointing at this checkout during development. Declare `rules_elide` directly for generated BUILD files. Copy the rules_elide archive override (use `@rules_mx//mx:elide_javabuilder.patch` for its patch label), the Elide installation and its toolchain registration from MODULE.bazel into the consuming root. Bazel applies archive overrides from the root module. For Gazelle, use the pinned Go SDK from MODULE.bazel. For the Python overlay, declare the matching rules_python runtime as shown in tools/BUILD.bazel and select it via `--extra_toolchains`. The CLI is available as `@rules_mx//:mx`. To address the bundled native targets directly, import the mx source repository:

```starlark
mx_sources = use_extension("@rules_mx//mx:extensions.bzl", "mx_sources")
use_repo(mx_sources, "graal_mx")
```

## Remote cache and execution

```sh
bazelisk build --config=rbc --remote_cache="$CACHE_ENDPOINT" //:webserver
bazelisk build --config=rbe --remote_executor="$EXECUTOR_ENDPOINT" //:webserver
```

The RBE profile requests Linux x86_64 execution and target platforms, a pinned Linux C++ toolchain for Bazel's auxiliary tools, remote spawn execution and minimal output downloads. Supply any service-specific instance name, worker image digest and authentication in your own Bazel configuration. Cache reuse requires matching execution platforms, toolchain inputs, build options and execution properties. To inspect the execution platform and inputs for each Java compilation:

```sh
bazelisk aquery --config=rbe 'mnemonic("ElideJavac", deps(//tests/gazelle/src/main/java/example/app))'
```

## BuildBuddy

With `BUILDBUDDY_API_KEY` in your local environment, run:

```sh
tools/buildbuddy test --config=elide-processors //...
tools/buildbuddy test --config=buildbuddy-rbe --config=elide-processors //tests/processing:outputs_test
```

Both commands use the `elide-dev/rules_mx` cache instance. The RBE profile fixes the target and execution CPU settings so macOS and Linux clients produce the same Linux output paths. The second also sends actions to the tenant's Linux AMD64 executors with automatic pool selection.

On pushes to `main` and manual runs, the Bazel workflow uses the repository's `BUILDBUDDY_API_KEY` secret and runs the full processor-enabled suite and the exporter tests with Labs JDK and Elide Python on BuildBuddy. Pull requests run the same tests on the GitHub runner without the cluster credential. Open the invocation link in the job log to inspect remote actions and cache hits.

## Releases

Use Conventional Commits such as `feat: export a suite dependency` or `fix: preserve processor order`. Release Please opens a release PR on `main` with the changelog and Bazel module version. Review and merge that PR to create the version tag and GitHub release.

## Verification

The initial Linux x86_64 REAPI smoke build compiled and packaged the real Graal SDK `org.graalvm.collections` project. The jar contained `EconomicMap.class` and `META-INF/native-image/org.graalvm.sdk/collections/native-image.properties`. The exporter regression suite passed remotely with CPython and with the Elide overlay. A fresh output base recovered all six compile and packaging actions for the webserver and the two-package Gazelle fixture from an HTTP cache. An implementation-only edit to the fixture's API package reused the consumer compilation.

## Local Java workers

Ordinary Java builds use Elide persistent workers and emit the ABI alongside the class jar for eligible targets. For local builds with explicitly modeled processors, `bazelisk build --config=elide-workers //tests/gazelle/... //:webserver` also enables multiplex workers and digest-keyed classpath reuse. The classpath cache disables implicit processor discovery. Use the separate RBE profile for remote execution.

The module pins Native Image toolchain PR #134 at `9d997fb`, which includes unified JVM builder PR #132 and applies the JavaBuilder processor fix from PR #133. With a registered Elide runtime advertising unified JVM request version 1, `--config=elide-unified` enables fused compilation for eligible Elide Java targets. The default Elide 1.6.2 distribution uses the legacy path. Processor consumers use the JavaBuilder adapter via `--config=elide-processors`.

## Labs JDK

`bazelisk run --config=labsjdk //:mx -- --version` selects the Oracle Labs JDK 25 JVMCI build pinned by the bundled mx suite. The profile selects the target Java runtime independently of the JDK used to compile Bazel tools. The extension provides checksum-pinned archives for Linux AMD64, Linux ARM64, and macOS ARM64. Consumers can register the same runtime toolchains using the Labs JDK stanza in MODULE.bazel.

## Native Image

Build mx's copyright checker with `bazelisk build //tests/native_image:checkcopy`. For Linux AMD64 remote execution, run `tools/buildbuddy test --config=buildbuddy-rbe --config=native-image-linux //tests/native_image:checkcopy_test`. The target uses the ordinary `rules_graalvm` Native Image rule with the SDK from Elide. Its Java input is the same project JAR used in JVM builds. The root module connects the rulesets with `inject_repo(elide, "rules_graalvm")` and registers `@elide_toolchains//graalvm:all`.

Native C/C++ toolchains use the checksum-pinned `elide-dev/toolchain` LLVM 23 archives also used in Komodo. Linux native actions use the archive's Clang, lld, headers and libc sysroot. To reproduce the Linux build from a Mac, put Bazel's output user root on a case-sensitive volume. For the BuildBuddy wrapper, set `BAZEL_OUTPUT_USER_ROOT` to a directory on that volume. The Linux worker image is pinned by digest and its build actions request network isolation. Downstream roots can select the same compiler registrations with `register_toolchains("@rules_mx//tools/cc:all")`.

For cacheable native macOS builds across machines, pin an Apple SDK archive in the consuming root module. Set `urls` and `sha256` to your team's archive and digest. Use `strip_prefix` when the archive wraps the SDK in a directory.

```starlark
llvm = use_extension("@rules_mx//tools/cc:extensions.bzl", "llvm")
llvm.darwin_sdk(
    urls = [APPLE_SDK_ARCHIVE_URL],
    sha256 = APPLE_SDK_SHA256,
    strip_prefix = "MacOSX26.4.sdk",
)
register_toolchains("@rules_mx//tools/cc:all")
```

The pinned SDK supplies the Darwin system headers, framework stubs and libraries as Bazel action inputs. The default macOS developer configuration selects an installed Xcode or Command Line Tools SDK at repository setup and tracks its files as compiler inputs. The compiler executes against the declared SDK in both modes. Linux comes with its sysroot in the compiler archive.
