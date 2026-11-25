package graal;

import java.nio.charset.StandardCharsets;
import java.nio.file.Path;

import org.graalvm.shadowed.com.ibm.icu.text.DecimalFormatSymbols;
import org.graalvm.shadowed.com.ibm.icu.util.ULocale;
import org.graalvm.shadowed.org.antlr.v4.runtime.CharStreams;
import org.graalvm.shadowed.org.json.JSONObject;
import org.junit.Test;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

public final class TruffleDistributionTest {
    @Test
    public void relocatedLibrariesReadLocaleData() {
        assertEquals(',', DecimalFormatSymbols.getInstance(ULocale.GERMAN).getDecimalSeparator());
        assertEquals(42, new JSONObject("{\"answer\":42}").getInt("answer"));
        assertEquals("mx", CharStreams.fromString("mx").toString());
    }

    @Test
    public void jmhDiscoversGeneratedBenchmarks() throws Exception {
        var java = Path.of(System.getProperty("java.home"), "bin", "java");
        var process = new ProcessBuilder(java.toString(), "-jar", System.getenv("JMH_JAR"),
                "-l", "ByteArrayAccessCloneBenchmark.systemArrayCopy").redirectErrorStream(true).start();
        var output = new String(process.getInputStream().readAllBytes(), StandardCharsets.UTF_8);
        assertEquals(output, 0, process.waitFor());
        assertTrue(output, output.lines().anyMatch(
                "org.graalvm.truffle.benchmark.ByteArrayAccessCloneBenchmark.systemArrayCopy"::equals));
    }
}
