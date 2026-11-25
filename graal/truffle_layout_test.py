"""Exercise the GraalVM support archives and run SimpleLanguage from its benchmark JAR."""

from pathlib import Path
import subprocess
import sys
import tarfile
import zipfile


def members(archive):
    with tarfile.open(archive) as bundle:
        files = bundle.getmembers()
        assert all(file.isfile() for file in files)
        names = bundle.getnames()
        assert len(names) == len(set(names)), names
        return set(names)


def main(
    launcher, sl, nfi_scripts, attach, libffi, nfi, headers, runtime, pmh, *properties
):
    for archive in properties:
        assert members(archive) == {"native-image.properties"}
        with tarfile.open(archive) as bundle:
            assert bundle.extractfile("native-image.properties").read().strip()

    (library,) = members(runtime)
    assert library in {"libtrufflenfi.so", "libtrufflenfi.dylib"}
    assert members(headers) == {"include/trufflenfi.h"}
    native = members(nfi)
    header_members = {"bin/include/trufflenfi.h", "include/trufflenfi.h"}
    assert header_members <= native, native
    (target_lib,) = native - header_members
    assert target_lib.startswith("bin/") and target_lib.endswith("/" + library), native
    target = target_lib.removeprefix("bin/").rsplit("/", 1)[0]
    assert members(libffi) == {
        target + "/libffi.a",
        target + "/include/ffi.h",
        target + "/include/ffitarget.h",
    }
    extension = Path(library).suffix
    assert members(attach) == {"libtruffleattach" + extension}
    assert members(pmh) == {"nfi-native/libmicrobench" + extension}

    with zipfile.ZipFile(nfi_scripts) as bundle:
        names = bundle.namelist()
        assert "nfi/downcall_simple.pmh" in names, names
        assert any(name.startswith("nfi/panama/") for name in names), names
        assert all(name.startswith("nfi/") and name.endswith(".pmh") for name in names)
    with zipfile.ZipFile(sl) as bundle:
        assert set(bundle.namelist()) == {
            "interpreter/fibonacci.sl",
            "interpreter/property_read.sl",
        }
        benchmark = bundle.read("interpreter/fibonacci.sl").decode("utf-8")
    result = subprocess.run(
        [Path(launcher).absolute(), "--disable-launcher-output"],
        input=benchmark + "\nfunction main() { println(run()); }\n",
        check=True,
        text=True,
        capture_output=True,
    )
    assert result.stdout == "1346269\n", result.stdout


if __name__ == "__main__":
    main(*sys.argv[1:])
