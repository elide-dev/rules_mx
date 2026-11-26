package graal;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import java.io.File;
import org.graalvm.polyglot.Context;
import org.graalvm.polyglot.Source;
import org.junit.Test;

public class SulongRuntimeTest {
    @Test
    public void executesBitcodeFromEspressoDistributionClosure() throws Exception {
        try (Context context = Context.newBuilder("llvm")
                .allowAllAccess(true)
                .option("engine.WarnInterpreterOnly", "false")
                .build()) {
            assertTrue(context.getEngine().getLanguages().containsKey("java"));
            var module = context.eval(Source.newBuilder("llvm", new File(System.getProperty("sulong.fixture"))).build());
            assertEquals(42, module.invokeMember("answer").asInt());
        }
    }
}
