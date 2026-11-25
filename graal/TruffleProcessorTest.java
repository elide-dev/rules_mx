// The generated factories have package visibility. Reference them from a separate
// Bazel target so javac must resolve them from the library's compile-time ABI.
package com.oracle.truffle.api.library;

import static org.junit.Assert.assertEquals;

import org.junit.Test;

public class TruffleProcessorTest {
    @Test
    public void generatedFactoriesAreInTheCompileAndRuntimeJars() {
        assertEquals(LibraryFactory.class, DynamicDispatchLibraryGen.class.getSuperclass());
        assertEquals(LibraryFactory.class, ReflectionLibraryGen.class.getSuperclass());
    }
}
