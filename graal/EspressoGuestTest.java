package graal;

import static org.junit.Assert.assertEquals;

import org.graalvm.polyglot.Context;
import org.junit.Test;

public class EspressoGuestTest {
    @Test
    public void executesJavaInTheGuestJdk() {
        try (Context context = Context.newBuilder("java")
                .allowAllAccess(true)
                .allowExperimentalOptions(true)
                .option("engine.WarnInterpreterOnly", "false")
                .option("java.JavaHome", System.getProperty("java.home"))
                .build()) {
            var bindings = context.getBindings("java");
            var list = bindings.getMember("java.util.ArrayList").newInstance();
            list.invokeMember("add", "espresso");
            assertEquals(1, list.invokeMember("size").asInt());
            assertEquals("espresso", list.invokeMember("get", 0).asString());
        }
    }
}
