package graal;

import com.google.testing.junit.runner.BazelTestRunner;

/** Run the selected upstream JUnit class in its declared JPMS module. */
public final class NamedModuleJUnit {
    public static void main(String[] args) throws Exception {
        String name = System.getenv("MX_TEST_CLASS");
        Class<?> test = Class.forName(name);
        if (!test.getModule().isNamed()) {
            throw new IllegalStateException(name + " loaded from the classpath");
        }
        System.setProperty("bazel.test_suite", name);
        BazelTestRunner.main(args);
    }
}
