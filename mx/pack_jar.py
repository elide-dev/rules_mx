"""Assemble resolved mx archive members, JNI services and a JPMS descriptor."""

import argparse
from collections import defaultdict
import os
from pathlib import Path
import re
import subprocess
import tempfile
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


def read_members(inputs, args):
    snippets = re.compile(args.snippets_pattern) if args.snippets_pattern else None
    entries = {}
    services = defaultdict(lambda: defaultdict(set))
    for jar in inputs:
        with ZipFile(jar) as src:
            for name in src.namelist():
                if name.endswith("/") or name.upper() == "META-INF/MANIFEST.MF":
                    continue
                if (
                    snippets
                    and jar not in args.archived_library
                    and not name.startswith("META-INF/services/")
                    and snippets.match(name)
                ):
                    continue
                data = src.read(name)
                marker = re.fullmatch(
                    r"(?:META-INF/versions/([1-9][0-9]*)/)?META-INF/(jni-entry-points|truffle-registrations|providers)/([^/]+)",
                    name,
                )
                if marker and marker[2] == "providers" and args.graal_test_services:
                    continue
                if marker and (
                    (args.graal_services and marker[2] == "providers")
                    or (args.jni_services and marker[2] == "jni-entry-points")
                    or (args.truffle_services and marker[2] == "truffle-registrations")
                ):
                    version, _, provider = marker.groups()
                    for service in data.decode().strip().splitlines():
                        if not service.strip():
                            raise ValueError(f"Empty service in {name}")
                        services[int(version or 0)][service].add(provider)
                elif service := re.fullmatch(r"META-INF/services/([^/]+)", name):
                    providers = services[0][service[1]]
                    for line in data.decode().splitlines():
                        if provider := line.partition("#")[0].strip():
                            providers.add(provider)
                elif args.jmh_metadata and name in {
                    "META-INF/BenchmarkList",
                    "META-INF/CompilerHints",
                }:
                    entries[name] = entries.get(name, b"") + data
                elif name in entries and entries[name] != data:
                    raise ValueError(f"Conflicting archive member {name} in {jar}")
                else:
                    entries[name] = data
                    if args.graal_services and name.endswith(
                        "_OptionDescriptors.class"
                    ):
                        version, _, entry = name.removeprefix(
                            "META-INF/versions/"
                        ).partition("/")
                        versioned = name.startswith("META-INF/versions/")
                        provider = (
                            (entry if versioned else name)
                            .removesuffix(".class")
                            .replace("/", ".")
                        )
                        services[int(version) if versioned else 0][
                            "jdk.graal.compiler.options.OptionDescriptors"
                        ].add(provider)
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


def module_descriptor(args, tmp, entries, services, release):
    if any(version > 0 for version in services) or any(
        n.startswith("META-INF/versions/") for n in entries
    ):
        raise ValueError(
            "Multi-release module descriptors require per-release compilation"
        )
    directives = set()
    packages = {
        name.rpartition("/")[0].replace("/", ".")
        for name in entries
        if name.endswith(".class") and "/" in name and not name.startswith("META-INF/")
    }
    for directive in args.directive:
        wildcard = re.fullmatch(r"(opens|exports) ([\w.]+)\*(.*)", directive)
        if wildcard:
            kind, prefix, targets = wildcard.groups()
            matches = [p for p in packages if p.startswith(prefix)]
            if not matches:
                raise ValueError(f"No compiled packages match {directive}")
            directives.update(f"{kind} {p}{targets}" for p in matches)
        else:
            directives.add(directive)
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
        *(
            ["-source", str(release), "-target", str(release)]
            if args.system_modules
            else ["--release", str(release)]
        ),
        "-proc:none",
        "-Xlint:-module",
        "-d",
        str(tmp),
        "--patch-module",
        f"{args.module}={classes}",
    ]
    if args.module_jar:
        # Prefer the source-built modules over their counterparts in the Labs JDK.
        option = "--upgrade-module-path" if args.system_modules else "--module-path"
        command += [option, os.pathsep.join(args.module_jar)]
    subprocess.run(command + [str(source)], check=True)
    return (tmp / "module-info.class").read_bytes()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", action="append", required=True)
    parser.add_argument(
        "--overlay", nargs=2, action="append", default=[], metavar=("VERSION", "JAR")
    )
    parser.add_argument("--output", required=True)
    parser.add_argument("--snippets-pattern")
    parser.add_argument("--archived-library", action="append", default=[])
    parser.add_argument("--graal-test-services", action="store_true")
    parser.add_argument("--graal-services", action="store_true")
    parser.add_argument("--truffle-services", action="store_true")
    parser.add_argument("--jmh-metadata", action="store_true")
    parser.add_argument("--jni-services", action="store_true")
    parser.add_argument("--manifest-entry", action="append", default=[])
    parser.add_argument("--module")
    parser.add_argument("--directive", action="append", default=[])
    parser.add_argument("--module-jar", action="append", default=[])
    parser.add_argument("--system-modules", action="store_true")
    parser.add_argument("--release", type=int)
    parser.add_argument("--java")
    args = parser.parse_args()
    entries, services = read_members(args.input, args)
    releases = defaultdict(list)
    for version, jar in args.overlay:
        if int(version) < 9:
            raise ValueError(f"Invalid multi-release JAR version {version}")
        releases[int(version)].append(jar)
    visible = dict(entries)
    with tempfile.TemporaryDirectory() as tmp:
        if args.module and 9 not in releases:
            entries["module-info.class"] = module_descriptor(
                args, Path(tmp), visible, services, args.release
            )
        for version, jars in sorted(releases.items()):
            layer, layer_services = read_members(jars, args)
            if any(layer_services):
                raise ValueError("An overlay cannot contain nested versioned providers")
            for service, providers in layer_services.get(0, {}).items():
                member = "META-INF/services/" + service
                del layer[member]
                services[0][service].update(providers)
                entries[f"META-INF/_versions/{version}/{member}"] = (
                    "\n".join(sorted(services[0][service])) + "\n"
                ).encode()
            if any(n.startswith("META-INF/") for n in layer):
                raise ValueError(
                    "Overlay META-INF resources need an explicit versioned layout"
                )
            visible.update(layer)
            if args.module:
                layer["module-info.class"] = module_descriptor(
                    args, Path(tmp), visible, services, version
                )
            entries.update(
                {
                    f"META-INF/versions/{version}/{name}": data
                    for name, data in layer.items()
                }
            )
    if releases:
        args.manifest_entry.append("Multi-Release=true")
    if args.manifest_entry:
        lines = ["Manifest-Version: 1.0"]
        for entry in args.manifest_entry:
            key, value = entry.split("=", 1)
            lines.append(f"{key}: {value}")
        entries["META-INF/MANIFEST.MF"] = ("\r\n".join(lines) + "\r\n\r\n").encode()
    write_jar(args.output, entries)


if __name__ == "__main__":
    main()
