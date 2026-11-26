package graal;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertTrue;

import org.graalvm.polyglot.Engine;
import org.junit.Test;

public class SulongLanguageTest {
    @Test
    public void engineDiscoversPackagedLanguageAndOptions() {
        try (Engine engine = Engine.newBuilder().option("engine.WarnInterpreterOnly", "false").build()) {
            var llvm = engine.getLanguages().get("llvm");
            assertNotNull("Sulong language provider", llvm);
            assertEquals("application/x-llvm-ir-bitcode", llvm.getDefaultMimeType());
            assertTrue(llvm.getMimeTypes().contains("application/x-executable"));
            assertNotNull(llvm.getOptions().get("llvm.parseOnly"));
        }
    }
}
