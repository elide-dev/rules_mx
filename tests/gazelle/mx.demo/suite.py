suite = {
    "name": "demo",
    "mxversion": "7.89.0",
    "projects": {
        "demo": {"dir": ".", "sourceDirs": ["src/main/java"], "javaCompliance": "17+"},
        "client": {
            "sourceDirs": ["src/main/java"],
            "javaCompliance": "17+",
            "dependencies": ["demo"],
        },
        "check": {
            "sourceDirs": ["src"],
            "javaCompliance": "17+",
            "testProject": True,
            "dependencies": ["demo", "client", "mx:JUNIT"],
        },
    },
    "distributions": {
        "BASE_LAYOUT": {
            "native": True,
            "layout": {"docs/suite.py": "file:mx.demo/suite.py"},
        },
        "NESTED_LAYOUT": {
            "native": True,
            "layout": {
                "share/": "extracted-dependency:BASE_LAYOUT/docs/suite.py",
                "archives/base.tar": "dependency:BASE_LAYOUT",
            },
        },
    },
}
