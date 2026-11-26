"""Copy a JAR and set its Automatic-Module-Name manifest attribute."""

import sys
import zipfile

MANIFEST = "META-INF/MANIFEST.MF"


def main(source, output, module):
    with zipfile.ZipFile(source) as jar, zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as copy:
        names = jar.namelist()
        lines = jar.read(MANIFEST).decode().splitlines() if MANIFEST in names else ["Manifest-Version: 1.0"]
        lines = [line for line in lines if line and not line.startswith("Automatic-Module-Name:")]
        manifest = "\r\n".join(lines + ["Automatic-Module-Name: " + module]) + "\r\n\r\n"
        info = zipfile.ZipInfo(MANIFEST, date_time=(2010, 1, 1, 0, 0, 0))
        copy.writestr(info, manifest)
        for entry in jar.infolist():
            if entry.filename != MANIFEST:
                copy.writestr(entry, jar.read(entry.filename))


if __name__ == "__main__":
    main(*sys.argv[1:])
