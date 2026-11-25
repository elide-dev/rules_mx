"""Assemble resolved mx archive members, JNI services and a JPMS descriptor."""

import argparse
from collections import defaultdict
import os
from pathlib import Path
import re
import subprocess
import tempfile
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


def read_members(inputs, jni_services):
    entries = {}
    services = defaultdict(lambda: defaultdict(set))
    for jar in inputs:
        with ZipFile(jar) as src:
            for name in src.namelist():
                if name.endswith("/") or name.upper() == "META-INF/MANIFEST.MF":
                    continue
                data = src.read(name)
                marker = re.fullmatch(
                    r"(?:META-INF/versions/([1-9][0-9]*)/)?META-INF/jni-entry-points/([^/]+)",
                    name,
                )
                if jni_services and marker:
                    version, provider = marker.groups()
                    for service in data.decode().strip().splitlines():
                        if not service.strip():
                            raise ValueError(f"Empty service in {name}")
                        services[int(version or 0)][service].add(provider)
                elif service := re.fullmatch(r"META-INF/services/([^/]+)", name):
                    providers = services[0][service[1]]
                    for line in data.decode().splitlines():
                        if provider := line.partition("#")[0].strip():
                            providers.add(provider)
                elif name in entries and entries[name] != data:
                    raise ValueError(f"Conflicting archive member {name} in {jar}")
                else:
                    entries[name] = data
    accumulated = defaultdict(set)
    for version, group in sorted(services.items()):
        for service, providers in group.items():
            accumulated[service].update(providers)
            prefix = f"META-INF/_versions/{version}/" if version else ""
            entries[f"{prefix}META-INF/services/{service}"] = (
                "\n".join(sorted(accumulated[service])) + "\n"
            ).encode()
    return entries, services


def write_jar(path, entries):
    with ZipFile(path, "w") as archive:
        for name in sorted(
            entries, key=lambda name: (name != "META-INF/MANIFEST.MF", name)
        ):
            content = entries[name]
            info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, content, compress_type=ZIP_DEFLATED)


def module_descriptor(args, tmp, entries, services):
    if any(version > 0 for version in services) or any(
        n.startswith("META-INF/versions/") for n in entries
    ):
        raise ValueError(
            "Multi-release module descriptors require per-release compilation"
        )
    directives = set(args.directive)
    for service, providers in services.get(0, {}).items():
        source_name = service.replace("$", ".")
        if service.replace(".", "/") + ".class" in entries:
            directives.add("uses " + source_name)
        if providers:
            names = sorted(p.replace("$", ".") for p in providers)
            directives.add(f"provides {source_name} with " + ", ".join(names))
    source = tmp / "module-info.java"
    source.write_text(
        f"module {args.module} {{\n"
        + "".join(f"  {s};\n" for s in sorted(directives))
        + "}\n",
        encoding="utf-8",
    )
    classes = tmp / "members.jar"
    write_jar(classes, entries)
    # The JDK is an execution-toolchain input, including its compiler module.
    command = [
        args.java,
        "-m",
        "jdk.compiler/com.sun.tools.javac.Main",
        "--release",
        str(args.release),
        "-proc:none",
        "-Xlint:-module",
        "-d",
        str(tmp),
        "--patch-module",
        f"{args.module}={classes}",
    ]
    if args.module_jar:
        command += ["--module-path", os.pathsep.join(args.module_jar)]
    subprocess.run(command + [str(source)], check=True)
    return (tmp / "module-info.class").read_bytes()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", action="append", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--jni-services", action="store_true")
    parser.add_argument("--manifest-entry", action="append", default=[])
    parser.add_argument("--module")
    parser.add_argument("--directive", action="append", default=[])
    parser.add_argument("--module-jar", action="append", default=[])
    parser.add_argument("--release", type=int)
    parser.add_argument("--java")
    args = parser.parse_args()
    entries, services = read_members(args.input, args.jni_services)
    if args.module:
        with tempfile.TemporaryDirectory() as tmp:
            entries["module-info.class"] = module_descriptor(
                args, Path(tmp), entries, services
            )
    if args.manifest_entry:
        lines = ["Manifest-Version: 1.0"]
        for entry in args.manifest_entry:
            key, value = entry.split("=", 1)
            lines.append(f"{key}: {value}")
        entries["META-INF/MANIFEST.MF"] = ("\r\n".join(lines) + "\r\n\r\n").encode()
    write_jar(args.output, entries)


if __name__ == "__main__":
    main()
