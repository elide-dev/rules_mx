package graal;

import java.io.InputStream;
import java.lang.module.ModuleDescriptor;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.util.jar.JarFile;

import org.junit.Test;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

public final class PanamaModuleTest {
    @Test
    public void panamaOverlayAddsItsProviderToTheVersionedDescriptor() throws Exception {
        var file = Path.of(System.getenv("PANAMA_JAR")).toFile();
        try (var jar = new JarFile(file)) {
            assertTrue(jar.isMultiRelease());
            var service = "com.oracle.truffle.api.library.provider.EagerExportProvider";
            var provider = "com.oracle.truffle.nfi.backend.panama.NativePointerGen$BackendNativePointerLibraryEagerProvider";
            var registry = "META-INF/_versions/22/META-INF/services/" + service;
            try (InputStream list = jar.getInputStream(jar.getJarEntry(registry))) {
                assertEquals(provider, new String(list.readAllBytes(), StandardCharsets.UTF_8).strip());
            }
            try (InputStream base = jar.getInputStream(jar.getJarEntry("module-info.class"));
                 InputStream overlay = jar.getInputStream(jar.getJarEntry("META-INF/versions/22/module-info.class"))) {
                assertFalse(ModuleDescriptor.read(base).provides().stream().anyMatch(p -> p.service().equals(service)));
                if (Runtime.version().feature() >= 22) {
                    assertTrue(ModuleDescriptor.read(overlay).provides().stream().anyMatch(
                            p -> p.service().equals(service) && p.providers().contains(provider)));
                }
            }
        }
    }
}
