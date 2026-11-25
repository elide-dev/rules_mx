"""Relocate and patch declared mx shaded-library sources before compilation."""

import argparse
import json
from pathlib import Path, PurePosixPath
import re
from zipfile import ZipFile

from pack_jar import write_jar


def shade(args):
    spec = json.loads(Path(args.spec).read_text())
    packages = spec["packages"]
    paths = {k.replace(".", "/"): v.replace(".", "/") for k, v in packages.items()}
    substitutions = [
        (re.compile(r"\b" + re.escape(k) + r"\b"), v) for k, v in packages.items()
    ]
    for k, v in paths.items():
        for prefix in ('"', '"/'):
            substitutions.append(
                (
                    re.compile(
                        r"(?<=" + prefix + ")" + re.escape(k) + r'(?=/[\w./]+")'
                    ),
                    v,
                )
            )
    includes = spec.get("include", {})
    if isinstance(includes, list):
        includes = dict.fromkeys(includes)
    sources, resources = {}, {}
    for jar in args.input:
        selected = {}
        with ZipFile(jar) as archive:
            for entry in archive.infolist():
                path = PurePosixPath(entry.filename)
                if (
                    entry.is_dir()
                    or path.suffix == ".class"
                    or any(path.full_match(p) for p in spec.get("exclude", []))
                ):
                    continue
                dest = entry.filename
                version = 0
                if path.parts[:2] == ("META-INF", "versions"):
                    version = int(path.parts[2])
                    dest = "/".join(path.parts[3:])
                if version > args.release:
                    continue
                java = path.suffix == ".java"
                mappings = paths
                if not java:
                    pattern = next((p for p in includes if path.full_match(p)), None)
                    if pattern is None:
                        continue
                    mappings = includes[pattern] or paths
                for old, new in mappings.items():
                    if dest.startswith(old):
                        dest = dest.replace(old, new)
                        break
                if dest == "module-info.java":
                    continue  # mx builds the distribution descriptor from moduleInfo.
                if dest in selected and selected[dest][0] >= version:
                    continue
                patches = [
                    (re.compile(k, re.MULTILINE), v)
                    for pattern, replacements in spec.get("patch", {}).items()
                    if path.full_match(pattern)
                    for k, v in replacements.items()
                ]
                transforms = (substitutions if java else []) + patches
                data = archive.read(entry)
                if transforms:
                    content = re.sub(r"(\n)?\s*(\n|$)", r"\1\2", data.decode("utf-8"))
                    for regex, value in transforms:
                        content = regex.sub(value, content)
                    data = (("// @formatter:off\n" if java else "") + content).encode()
                selected[dest] = version, data
        for dest, (_, data) in selected.items():
            (sources if dest.endswith(".java") else resources)[dest] = data
    for dest, path in args.resource:
        resources[dest] = Path(path).read_bytes()
    write_jar(args.srcjar, sources)
    write_jar(args.resources, resources)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", required=True)
    parser.add_argument("--release", type=int, required=True)
    parser.add_argument("--srcjar", required=True)
    parser.add_argument("--resources", required=True)
    parser.add_argument("--input", action="append", required=True)
    parser.add_argument("--resource", nargs=2, action="append", default=[])
    shade(parser.parse_args())
