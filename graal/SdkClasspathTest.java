package graal;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotNull;

import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import org.junit.Test;

public class SdkClasspathTest {
    @Test
    public void exposesVersionFilesAtTheirMxPaths() throws Exception {
        for (String artifact : new String[]{"org.graalvm.polyglot", "org.graalvm.maven.downloader"}) {
            String path = "/META-INF/graalvm/" + artifact + "/version";
            try (InputStream stream = SdkClasspathTest.class.getResourceAsStream(path)) {
                assertNotNull(path, stream);
                assertEquals(path, "25.5.5", new String(stream.readAllBytes(), StandardCharsets.UTF_8));
            }
        }
    }
}
