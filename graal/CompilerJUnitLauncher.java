package com.oracle.mxtool.junit;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

/** Forward Bazel's test environment to the compiler VM and its upstream subprocess harness. */
public final class CompilerJUnitLauncher {
    public static void main(String[] args) throws Exception {
        String test = System.getenv("MX_TEST_CLASS");
        if (test == null) {
            throw new IllegalArgumentException("Run the compiler JUnit class by its Bazel test label.");
        }
        Path tmp = Path.of(System.getenv("TEST_TMPDIR"));
        Path commandFile = tmp.resolve("jvm-command");
        var command = new ArrayList<>(List.of(args));
        command.addAll(1, List.of("-Dbazel.test_suite=" + test,
                        "-Dpolyglot.engine.userResourceCache=" + tmp,
                        "-Dpolyglotimpl.AttachLibraryFailureAction=throw"));
        Files.write(commandFile, command);
        var process = new ProcessBuilder(command).inheritIO();
        process.environment().put("MX_SUBPROCESS_COMMAND_FILE", commandFile.toString());
        System.exit(process.start().waitFor());
    }
}
