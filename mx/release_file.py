"""Build mx release metadata from the target JDK and pinned source archive."""

import json
from pathlib import Path
import sys


def main(output, parent, provenance, version, *suites):
    fields = {}
    for line in Path(parent).read_text(encoding="utf-8").splitlines():
        if line.strip():
            key, value = line.strip().split("=", 1)
            fields[key] = (
                value[1:-1] if value.startswith('"') and value.endswith('"') else value
            )
    for required in ("JAVA_VERSION", "OS_NAME", "OS_ARCH"):
        if not fields.get(required):
            raise ValueError(f"Missing {required} in target JDK release file")
    commit = json.loads(Path(provenance).read_text(encoding="utf-8")).get("commit")
    if commit is not None and set(commit) != {
        "commit.rev",
        "commit.committer",
        "commit.committer-ts",
    }:
        raise ValueError("Incomplete mx commit metadata in source manifest")
    rev = commit["commit.rev"] if commit else None
    fields["GRAALVM_VERSION"] = version
    fields["SOURCE"] = " ".join(
        filter(None, [fields.get("SOURCE"), *(f"{suite}:{rev}" for suite in suites)])
    )
    fields["COMMIT_INFO"] = json.dumps(
        {suite: commit for suite in suites} if commit else {}, sort_keys=True
    )
    lines = [
        f"{k}={v}" if k == "COMMIT_INFO" else f'{k}="{v}"' for k, v in fields.items()
    ]
    Path(output).write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main(*sys.argv[1:])
