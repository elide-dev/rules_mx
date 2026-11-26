"""Checks the SDK and Truffle API references: their package sets, pages and archives."""

import json
import sys
import tarfile
import unittest
from pathlib import Path

SDK_PACKAGES = {
    "org.graalvm.collections",
    "org.graalvm.home",
    "org.graalvm.nativeimage",
    "org.graalvm.nativeimage.c",
    "org.graalvm.nativeimage.c.constant",
    "org.graalvm.nativeimage.c.function",
    "org.graalvm.nativeimage.c.struct",
    "org.graalvm.nativeimage.c.type",
    "org.graalvm.nativeimage.dynamicaccess",
    "org.graalvm.nativeimage.hosted",
    "org.graalvm.options",
    "org.graalvm.polyglot",
    "org.graalvm.polyglot.io",
    "org.graalvm.polyglot.management",
    "org.graalvm.polyglot.proxy",
    "org.graalvm.word",
}

# mx_truffle.javadoc: the SDK set, com.oracle.svm.core.annotate and the Truffle API projects.
TRUFFLE_PACKAGES = SDK_PACKAGES | {
    "com.oracle.svm.core.annotate",
    "com.oracle.truffle.api",
    "com.oracle.truffle.api.bytecode",
    "com.oracle.truffle.api.bytecode.debug",
    "com.oracle.truffle.api.bytecode.serialization",
    "com.oracle.truffle.api.debug",
    "com.oracle.truffle.api.dsl",
    "com.oracle.truffle.api.exception",
    "com.oracle.truffle.api.frame",
    "com.oracle.truffle.api.instrumentation",
    "com.oracle.truffle.api.instrumentation.provider",
    "com.oracle.truffle.api.interop",
    "com.oracle.truffle.api.io",
    "com.oracle.truffle.api.library",
    "com.oracle.truffle.api.library.provider",
    "com.oracle.truffle.api.memory",
    "com.oracle.truffle.api.nodes",
    "com.oracle.truffle.api.object",
    "com.oracle.truffle.api.profiles",
    "com.oracle.truffle.api.provider",
    "com.oracle.truffle.api.source",
    "com.oracle.truffle.api.staticobject",
    "com.oracle.truffle.api.strings",
    "com.oracle.truffle.api.utilities",
}

SDK_SITE, TRUFFLE_SITE, SDK_ARCHIVE, TRUFFLE_ARCHIVE = (Path(arg) for arg in sys.argv[1:5])
del sys.argv[1:5]


class ApiDocsTest(unittest.TestCase):
    def check(self, site, archive, title, packages, pages):
        listed = set(site.joinpath("element-list").read_text(encoding="utf-8").split())
        self.assertEqual(packages, listed)

        model = json.loads(site.joinpath("api.json").read_text(encoding="utf-8"))
        self.assertEqual(title, model["module"]["name"])
        self.assertEqual(packages, {pkg["name"] for pkg in model["packages"]})

        for page in pages:
            self.assertTrue(site.joinpath(page).is_file(), page)
        # JDK types link to the JDK 25 reference through the checked-in element-list.
        polyglot = site.joinpath("org/graalvm/polyglot/Context.html").read_text(encoding="utf-8")
        self.assertIn("https://docs.oracle.com/en/java/javase/25/docs/api/java.base/", polyglot)

        with tarfile.open(archive) as tar:
            members = {m.name.removeprefix("./") for m in tar.getmembers() if m.isfile()}
        files = {str(p.relative_to(site)) for p in site.rglob("*") if p.is_file()}
        self.assertEqual(files, members)

    def test_sdk(self):
        self.check(
            SDK_SITE,
            SDK_ARCHIVE,
            "GraalVM SDK",
            SDK_PACKAGES,
            ["index.html", "org/graalvm/polyglot/Value.html", "org/graalvm/word/WordBase.html"],
        )
        # The implementation packages are left out, as `mx sdk javadoc` does.
        self.assertFalse(SDK_SITE.joinpath("org/graalvm/polyglot/impl").exists())

    def test_truffle(self):
        self.check(
            TRUFFLE_SITE,
            TRUFFLE_ARCHIVE,
            "Truffle",
            TRUFFLE_PACKAGES,
            [
                "index.html",
                "com/oracle/truffle/api/TruffleLanguage.html",
                "com/oracle/truffle/api/strings/TruffleString.html",
                "com/oracle/truffle/api/dsl/Specialization.html",
            ],
        )
        self.assertFalse(TRUFFLE_SITE.joinpath("com/oracle/truffle/api/impl").exists())


if __name__ == "__main__":
    unittest.main()
