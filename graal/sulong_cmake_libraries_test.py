"""Check the installed Sulong CMake libraries and their embedded guest bitcode."""

import hashlib
from pathlib import Path
import subprocess
import sys


def main(nm, objdump, graal, native, resources):
    for library, symbol in [
        (graal, "polyglot_import"),
        (native, "__sulong_posix_open"),
    ]:
        symbols = subprocess.check_output([nm, "--defined-only", library], text=True)
        assert any(
            row.split()[-1].lstrip("_") == symbol.lstrip("_")
            for row in symbols.splitlines()
            if row.strip()
        ), (library, symbol)
        section = "__bundle" if Path(library).suffix == ".dylib" else ".llvmbc"
        dumped = subprocess.check_output([objdump, "-s", "-j", section, library], text=True)
        words = [row.split()[1].lower() for row in dumped.splitlines() if row.startswith(" ") and len(row.split()) > 1]
        assert words and words[0] in {"4243c0de", "dec0170b"}, (library, section, words[:1])
    assert Path(graal).name in ("libgraalvm-llvm.so.1", "libgraalvm-llvm.1.dylib")
    assert Path(native).name in ("libsulong-native.so", "libsulong-native.dylib")

    if Path(native).suffix == ".so":
        tags = subprocess.check_output([objdump, "-p", native], text=True)
        needed = {fields[1] for row in tags.splitlines() if (fields := row.split()) and fields[0] == "NEEDED"}
        assert not needed.intersection({"libc++.so.1", "libc++abi.so.1"}), needed

    root = Path(resources)
    (manifest,) = root.glob("META-INF/resources/llvm/native-lib/*/*/files")
    library = manifest.parent / Path(native).name
    member = library.relative_to(root).as_posix()
    assert library.read_bytes() == Path(native).read_bytes()
    [(listed, mode)] = [line.split(" = ") for line in manifest.read_text().splitlines()]
    expected_mode = "rwxr-xr-x" if library.suffix == ".dylib" else "rw-r--r--"
    assert (listed, mode) == (member, expected_mode), (listed, mode)
    digest = hashlib.sha256(library.read_bytes() + member.encode()).hexdigest()
    assert (manifest.parent / "sha256").read_text() == digest


if __name__ == "__main__":
    main(*sys.argv[1:])
