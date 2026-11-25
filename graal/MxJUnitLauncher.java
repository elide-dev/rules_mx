package com.oracle.mxtool.junit;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

/** Forward Bazel's test environment to the test VM and its upstream subprocess harness. */
public final class MxJUnitLauncher {
    public static void main(String[] args) throws Exception {
        String test = System.getenv("MX_TEST_CLASS");
        if (test == null) {
            throw new IllegalArgumentException("Run the JUnit class by its Bazel test label.");
        }
        Path tmp = Path.of(System.getenv("TEST_TMPDIR"));
        Path commandFile = tmp.resolve("jvm-command");
        boolean strictAttach = Boolean.parseBoolean(args[0]);
        var command = new ArrayList<>(Arrays.asList(args).subList(1, args.length));
        command.add(1, "-Dbazel.test_suite=" + test);
        if (strictAttach) {
            command.addAll(1, List.of("-Dpolyglot.engine.userResourceCache=" + tmp,
                            "-Dpolyglotimpl.AttachLibraryFailureAction=throw"));
        } else {
            command.add(1, "-Duser.home=" + tmp);
        }
        Files.write(commandFile, command);
        var process = new ProcessBuilder(command).inheritIO();
        process.environment().put("MX_SUBPROCESS_COMMAND_FILE", commandFile.toString());
        System.exit(process.start().waitFor());
    }
}
