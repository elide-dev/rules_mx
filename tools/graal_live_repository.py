#!/usr/bin/env python3
"""Inventory live GraalVM checkouts and materialize Bazel's source repository."""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
OVERLAYS = ROOT / "graal"


def checkout_files(checkout):
    if (checkout / ".git").exists():
        files = (
            subprocess.check_output(
                ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                cwd=checkout,
            )
            .decode()
            .split("\0")
        )
        return sorted(
            {Path(file) for file in files if file and (checkout / file).is_file()}
        )
    return sorted(
        file.relative_to(checkout) for file in checkout.rglob("*") if file.is_file()
    )


def changed_paths(patch):
    # The checked-in overlays have simple unified-diff paths. Only copy existing
    # patched files so git apply never sees a writable path into either checkout.
    return [
        Path(name)
        for name in re.findall(r"^--- a/(.+)$", patch.read_text(), re.MULTILINE)
    ]


def apply_patch(root, patch):
    env = {**os.environ, "GIT_CEILING_DIRECTORIES": str(root.parent)}
    result = subprocess.run(
        ["git", "apply", "--unidiff-zero", "--reverse", "--check", str(patch)],
        cwd=root,
        capture_output=True,
        env=env,
    )
    if result.returncode:
        subprocess.run(
            ["git", "apply", "--unidiff-zero", str(patch)],
            cwd=root,
            check=True,
            env=env,
        )


def git_inputs(checkout):
    if not (checkout / ".git").exists():
        return []
    ref = subprocess.run(
        ["git", "symbolic-ref", "-q", "HEAD"],
        cwd=checkout,
        capture_output=True,
        text=True,
    ).stdout.strip()
    names = ["HEAD", "index", "packed-refs"]
    if ref:
        names.append(ref)
    return [
        checkout / path
        for path in subprocess.check_output(
            [
                "git",
                "rev-parse",
                *[flag for name in names for flag in ("--git-path", name)],
            ],
            cwd=checkout,
            text=True,
        ).splitlines()
    ]


def provenance(checkout):
    if not (checkout / ".git").exists():
        return {}
    rev, timestamp, committer = subprocess.check_output(
        ["git", "show", "-s", "--format=%H%n%ct%n%cn <%ce>", "HEAD"],
        cwd=checkout,
        text=True,
    ).splitlines()
    return {
        "commit": {
            "commit.rev": rev,
            "commit.committer-ts": int(timestamp),
            "commit.committer": committer,
        },
    }


def check_input_pins(generated):
    differences = []
    for file in sorted(generated.glob("*.json")):
        lock = (
            ROOT / "mx" / file.name
            if file.name in {"labsjdk.json", "labsjdk21.json"}
            else OVERLAYS / file.name
        )
        if not lock.is_file() or json.loads(file.read_text()) != json.loads(
            lock.read_text()
        ):
            differences.append(file.name)
    if differences:
        raise SystemExit(
            "This checkout requires different Bazel input pins. Update the rules_mx "
            "pins or select a compatible checkout. Changed pins "
            + ", ".join(differences)
        )


def scan(workspace, patches):
    graal = (workspace / "graal").resolve()
    if not (graal / "truffle/mx.truffle/suite.py").is_file():
        raise SystemExit(f"Expected a Graal checkout at {workspace / 'graal'}")
    checkouts = [graal]
    peer_paths = {}
    sources = {path: graal / path for path in checkout_files(graal)}
    watch_files = {workspace / "graal"}
    watch_dirs = {graal}
    for suite, spec in json.loads((OVERLAYS / "live_suites.json").read_text()).items():
        entry = workspace / spec["checkout"]
        checkout = entry.resolve()
        directory = Path(spec["path"])
        metadata = entry / directory / f"mx.{suite}/suite.py"
        watch_files.update([entry, metadata])
        if not metadata.is_file():
            continue
        checkouts.append(checkout)
        peer_paths[suite] = str(checkout / directory)
        watch_dirs.add(checkout / directory)
        sources.update(
            {
                Path(suite) / path.relative_to(directory): checkout / path
                for path in checkout_files(checkout)
                if path.is_relative_to(directory)
            }
        )
    roots = tuple(watch_dirs)
    for path, source in sources.items():
        for parent in source.parents:
            if parent in roots:
                break
            watch_dirs.add(parent)
        if path.name in {"common.json", "BUILD", "BUILD.bazel", ".gitignore"} or any(
            part.startswith("mx.") for part in path.parts
        ):
            watch_files.add(source)
    for checkout in checkouts:
        watch_files.update(git_inputs(checkout))
    for patch in patches:
        for path in changed_paths(patch):
            watch_files.add(sources.get(path, graal / path))
    print(
        json.dumps(
            {
                "graal": str(graal),
                "peers": peer_paths,
                "sources": {str(path): str(sources[path]) for path in sorted(sources)},
                "watch_files": sorted(map(str, watch_files)),
                "watch_dirs": sorted(map(str, watch_dirs)),
            }
        )
    )


def generate(repo, bazel, output_base, patches):
    inputs = json.loads((repo / ".graph-inputs.json").read_text())
    graal = Path(inputs["graal"])
    sources = {Path(path): Path(source) for path, source in inputs["sources"].items()}
    peer_flags = [
        arg
        for suite, path in inputs["peers"].items()
        for arg in ("--peer", f"{suite}={path}")
    ]
    with tempfile.TemporaryDirectory(dir=repo) as work:
        work = Path(work)
        generated = work / "generated"
        subprocess.run(
            [ROOT / "tools/generate-graal", graal, "--output", generated, *peer_flags],
            check=True,
            cwd=ROOT,
            env={
                **os.environ,
                "RULES_MX_GRAPH_BAZEL": bazel,
                "RULES_MX_GRAPH_OUTPUT_BASE": output_base,
            },
        )
        check_input_pins(generated)
        for patch in patches:
            paths = changed_paths(patch)
            for path in paths:
                if path not in sources:
                    raise SystemExit(f"{patch.name} requires {path} in {graal}")
                target = repo / path
                if not target.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(sources[path], target)
            if paths:
                apply_patch(repo, patch)
        for patch in sorted(generated.glob("*.patch")):
            apply_patch(repo, patch)
        root_files = {
            Path(name)
            for name in (
                "BUILD",
                "BUILD.bazel",
                "MODULE.bazel",
                "REPO.bazel",
                "WORKSPACE",
                "WORKSPACE.bazel",
                "live_suites.bzl",
                "mx-source.json",
            )
        }
        for path, source in sources.items():
            target = repo / path
            if not target.exists() and path not in root_files:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.symlink_to(source)
        (repo / "REPO.bazel").write_text("# Local overlay generated by rules_mx.\n")
        (repo / "BUILD.bazel").write_text('exports_files(["mx-source.json"])\n')
        (repo / "live_suites.bzl").write_text(
            "LIVE_SUITES = " + json.dumps(sorted(inputs["peers"])) + "\n"
        )
        (repo / "mx-source.json").write_text(
            json.dumps(provenance(graal), indent=2) + "\n"
        )


def main():
    command, location, *arguments = sys.argv[1:]
    if command == "scan":
        scan(Path(location), [Path(path) for path in arguments])
    else:
        bazel, output_base, *patches = arguments
        generate(Path(location), bazel, output_base, [Path(path) for path in patches])


if __name__ == "__main__":
    main()
