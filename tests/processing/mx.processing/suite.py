suite = {
    "name": "processing",
    "mxversion": "7.89.0",
    "projects": {
        "proc": {"sourceDirs": ["src"], "javaCompliance": "17+"},
        "app": {
            "sourceDirs": ["src"],
            "javaCompliance": "17+",
            "annotationProcessors": ["PROCESSORS"],
        },
        "consumer": {
            "dir": "consumer",
            "sourceDirs": ["src"],
            "javaCompliance": "17+",
            "dependencies": ["app"],
        },
    },
    "distributions": {"PROCESSORS": {"dependencies": ["proc"]}},
}
