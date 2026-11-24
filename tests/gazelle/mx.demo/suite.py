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
}
