package graal;

import static org.junit.Assert.*;

import java.io.IOException;
import java.lang.module.ModuleDescriptor;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.HexFormat;
import java.util.Locale;
import java.util.Set;
import java.util.jar.JarFile;
import org.junit.Test;

public class TruffleArchiveTest {
    private static byte[] contents(JarFile jar, String path) throws IOException {
        var entry = jar.getJarEntry(path);
        assertNotNull(path, entry);
        try (var input = jar.getInputStream(entry)) {
            return input.readAllBytes();
        }
    }

    @Test
    public void bothDescriptorsKeepQualifiedExportsAndGeneratedProviders() throws IOException {
        try (var jar = new JarFile(System.getenv("TRUFFLE_API"))) {
            assertTrue(jar.isMultiRelease());
            assertNull(jar.getJarEntry("module-info.class"));
            for (int version : new int[] {9, 21}) {
                String path = "META-INF/versions/" + version + "/module-info.class";
                var descriptor = ModuleDescriptor.read(java.nio.ByteBuffer.wrap(contents(jar, path)));
                assertEquals("org.graalvm.truffle", descriptor.name());
                var isolate = descriptor.exports().stream()
                        .filter(e -> e.source().equals("com.oracle.truffle.polyglot.isolate"))
                        .findFirst().orElseThrow();
                assertEquals(Set.of("org.graalvm.truffle.runtime.svm"), isolate.targets());
                assertTrue(descriptor.provides().stream().anyMatch(p ->
                        p.service().equals("org.graalvm.jniutils.JNIEntryPointProvider")));
            }
            assertFalse(jar.stream().anyMatch(e -> e.getName().contains("Snippets")));
        }
    }

    @Test
    public void attachLibraryHasTargetPlatformPathPermissionsAndContentHash() throws Exception {
        String osName = System.getProperty("os.name").toLowerCase(Locale.ROOT);
        String os = osName.startsWith("mac") ? "darwin" : osName.startsWith("windows") ? "windows" : osName;
        String arch = System.getProperty("os.arch").replace("x86_64", "amd64");
        String base = "META-INF/resources/engine/libtruffleattach/" + os + "/" + arch + "/";
        String library = base + "bin/" + System.mapLibraryName("truffleattach");
        try (var jar = new JarFile(System.getenv("TRUFFLE_API"))) {
            var digest = MessageDigest.getInstance("SHA-256");
            digest.update(contents(jar, library));
            digest.update(library.getBytes(StandardCharsets.UTF_8));
            assertEquals(HexFormat.of().formatHex(digest.digest()),
                    new String(contents(jar, base + "sha256"), StandardCharsets.UTF_8));
            assertEquals(library + " = rwxr-xr-x",
                    new String(contents(jar, base + "files"), StandardCharsets.UTF_8));
        }
    }
}
