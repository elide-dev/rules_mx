"""Expose installed mx files to archive consumers and repository definitions."""

MxArchiveLayoutInfo = provider(
    doc = "Mapping of packaged archive paths to their declared Bazel files.",
    fields = ["archive", "entries", "links", "jdk_homes", "primary", "resource_jar", "modes", "tar_trees", "image_groups"],
)
