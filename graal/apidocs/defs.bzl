"""API references of the Graal SDK and Truffle, rendered by elide-dev/doclet."""

load("@bazel_skylib//rules:common_settings.bzl", "BuildSettingInfo")
load("@rules_elide//elide:defs.bzl", "elide_apidocs")

# The public home of the published references: <PUBLIC_ROOT><name>/<version>/.
PUBLIC_ROOT = "https://static.elideusercontent.com/graalvm/javadoc/"

def _setting(target):
    return target[BuildSettingInfo].value

def _release_inputs_impl(ctx):
    version = _setting(ctx.attr._version)
    source_ref = _setting(ctx.attr._source_ref)
    theme = json.decode(ctx.attr.theme)
    if version:
        # Canonical URLs and a sitemap for the published copy.
        theme["baseUrl"] = PUBLIC_ROOT + ctx.attr.reference + "/" + version + "/"
    theme_file = ctx.actions.declare_file(ctx.label.name + ".json")
    ctx.actions.write(theme_file, json.encode_indent(theme, indent = "  ") + "\n")

    # Release-specific doclet options, read by javadoc as an @argfile.
    args = []
    if version:
        args.extend(["--module-version", version])
    if source_ref:
        args.extend(["--source-ref", source_ref])
    args_file = ctx.actions.declare_file(ctx.label.name + ".args")
    ctx.actions.write(args_file, "".join(["'{}'\n".format(arg) for arg in args]))
    return [
        DefaultInfo(files = depset([theme_file, args_file])),
        OutputGroupInfo(args = depset([args_file]), theme = depset([theme_file])),
    ]

_release_inputs = rule(
    implementation = _release_inputs_impl,
    doc = "Writes the theme and doclet arguments for the release named by the build settings.",
    attrs = {
        "reference": attr.string(mandatory = True, doc = "Path segment under PUBLIC_ROOT."),
        "theme": attr.string(mandatory = True, doc = "Base theme, as JSON."),
        "_source_ref": attr.label(default = ":source_ref"),
        "_version": attr.label(default = ":version"),
    },
)

def graal_apidocs(name, title, theme, packages, exclude_packages, deps, problems = None, tags = []):
    """Declares the `name` API reference and its release inputs.

    Args:
      name: Reference name, also its path segment under PUBLIC_ROOT.
      title: Reference title.
      theme: Base renderer theme, a dict.
      packages: Documented package roots (`-subpackages`).
      exclude_packages: Packages under `packages` that are left out.
      deps: Projects whose sources are documented; their compile classpath resolves the rest.
      problems: The doclet's `--problems` setting.
      tags: Tags of every target.
    """
    _release_inputs(
        name = name + "_release",
        reference = name,
        theme = json.encode(theme),
        tags = tags,
    )
    native.filegroup(
        name = name + "_theme",
        srcs = [":" + name + "_release"],
        output_group = "theme",
        tags = tags,
    )
    native.filegroup(
        name = name + "_args",
        srcs = [":" + name + "_release"],
        output_group = "args",
        tags = tags,
    )
    elide_apidocs(
        name = name,
        data = [":" + name + "_args"],
        deps = deps,
        doclet = "@elide_doclet//:doclet",
        exclude_packages = exclude_packages,
        javadoc_opts = ["@$(location :{}_args)".format(name)],
        jvm_flags = ["-Xmx4g"],
        module_name = title,
        offline_links = {"https://docs.oracle.com/en/java/javase/25/docs/api/": "jdk/element-list"},
        packages = packages,
        problems = problems,
        tags = tags,
        theme = ":" + name + "_theme",
        title = title,
        visibility = ["//visibility:public"],
    )
