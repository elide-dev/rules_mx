"""Package one NetBeans reactor module against a read-only offline Maven repository."""

import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile


def _archive(data, kind):
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(output, "w") as dest:
        for name in sorted(source.namelist()):
            entry = source.getinfo(name)
            body = source.read(entry)
            if kind == "nbm" and name == "Info/info.xml":
                body = re.sub(rb'releasedate="[^"]+"', b'releasedate="2000/01/01"', body)
            elif kind == "nbm" and name.startswith("netbeans/modules/") and name.count("/") == 2 and name.endswith(".jar"):
                body = _archive(body, "jar")
            entry.date_time = (2000, 1, 1, 0, 0, 0)
            entry.extra = b""
            dest.writestr(entry, body)
    return output.getvalue()


def _manifest(module, version):
    path = module / "src/main/nbm/manifest.mf"
    content = path.read_text() if path.exists() else "Manifest-Version: 1.0\n"
    main, separator, sections = content.replace("\r\n", "\n").partition("\n\n")
    fields = {"OpenIDE-Module-Build-Version": "200001010000",
              "OpenIDE-Module-Implementation-Version": version.replace("SNAPSHOT", "20000101")}
    for key, value in fields.items():
        if not re.search("^" + key + ":", main, re.M | re.I):
            main = main.rstrip("\n") + "\n" + key + ": " + value
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(main + (separator or "\n\n") + sections)


def main(spec_path):
    spec = json.loads(Path(spec_path).read_text())
    root = Path.cwd()
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        project = work / "reactor"
        module = project / spec["module"]
        module.mkdir(parents=True)
        parent = Path(spec["parent"])
        shutil.copyfile(parent, project / "pom.xml")
        for src in spec["srcs"]:
            file = Path(src)
            dest = project / file.relative_to(parent.parent)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(file, dest)
        repository = work / "repository"
        for target, file in spec["dependencies"].items():
            dest = repository / target
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.symlink_to(root / file)
        if spec["kind"] == "nbm":
            _manifest(module, spec["version"])
        maven = (root / spec["maven"]).parent
        boot, = (maven / "boot").glob("plexus-classworlds-*.jar")
        user = work / "home"
        user.mkdir()
        tools = work / "tools"
        tools.mkdir()
        (tools / "chmod").symlink_to(root / spec["coreutils"])
        java = root / spec["java"]
        args = [java, "-Djava.awt.headless=true", "-Duser.timezone=UTC",
                "-Duser.language=en", "-Duser.country=US", "-Dfile.encoding=UTF-8",
                "-Duser.home=" + str(user), "-Dmaven.home=" + str(maven),
                "-Dclassworlds.conf=" + str(maven / "bin/m2.conf"),
                "-Dmaven.multiModuleProjectDirectory=" + str(project),
                "-classpath", boot, "org.codehaus.plexus.classworlds.launcher.Launcher",
                "--offline", "--batch-mode", "--no-transfer-progress",
                "-Dmaven.repo.local=" + str(repository),
                "-Dmaven.repo.local.tail=" + str((root / spec["repository"]).parent),
                "-Dproject.build.outputTimestamp=2000-01-01T00:00:00Z", "-DskipTests", "package"]
        subprocess.run(args, cwd=module, env={"PATH": str(tools) + os.pathsep + str(java.parent), "JAVA_HOME": str(java.parent.parent),
                       "HOME": str(user), "LANG": "C", "TZ": "UTC", "SOURCE_DATE_EPOCH": "946684800"}, check=True)
        target = module / "target"
        for extension, filename in spec["outputs"].items():
            out = root / filename
            if extension == "application":
                shutil.copytree(target / spec["install"], out, dirs_exist_ok=True)
                (out / "bin" / spec["install"]).chmod(0o755)
                for file in out.glob("*/update_tracking/*.xml"):
                    file.write_bytes(re.sub(rb'install_time="[0-9]+"', b'install_time="946684800000"', file.read_bytes()))
            else:
                artifact = target / (spec["artifact"] + "-" + spec["version"] + extension)
                out.write_bytes(_archive(artifact.read_bytes(), extension.lstrip(".")))


if __name__ == "__main__":
    main(sys.argv[1])
