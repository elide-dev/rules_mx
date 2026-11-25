package graal.nativebridge;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertTrue;

import java.lang.module.ModuleFinder;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.util.List;
import java.util.ServiceLoader;
import java.util.Set;
import java.util.stream.Collectors;
import java.util.jar.JarFile;
import org.junit.Test;

public class BridgeModuleTest {
    @Test
    public void processorTestArchivePublishesEveryGeneratedJniProvider() throws Exception {
        try (var jar = new JarFile(System.getenv("NATIVEBRIDGE_PROCESSOR_TEST"))) {
            var entries = jar.stream().map(entry -> entry.getName()).toList();
            assertFalse(entries.stream().anyMatch(name -> name.contains("META-INF/jni-entry-points/")));
            var classes = entries.stream().filter(name -> name.endsWith("JNIProvider.class"))
                    .map(name -> name.substring(0, name.length() - 6).replace('/', '.')).collect(Collectors.toSet());
            assertEquals(22, classes.size());
            var service = jar.getJarEntry("META-INF/services/org.graalvm.jniutils.JNIEntryPointProvider");
            assertNotNull(service);
            try (var stream = jar.getInputStream(service)) {
                assertEquals(classes, new String(stream.readAllBytes(), StandardCharsets.UTF_8)
                        .lines().collect(Collectors.toSet()));
            }
        }
    }

    @Test
    public void resolvesNamedModulesAndLoadsGeneratedProviders() throws Exception {
        Path[] jars = List.of("WORD", "COLLECTIONS", "NATIVEIMAGE", "JNIUTILS", "NATIVEBRIDGE")
                .stream().map(name -> Path.of(System.getenv(name))).toArray(Path[]::new);
        var boot = ModuleLayer.boot();
        var config = boot.configuration().resolve(ModuleFinder.of(jars), ModuleFinder.of(),
                Set.of("org.graalvm.nativebridge"));
        var layer = boot.defineModulesWithOneLoader(config, ClassLoader.getPlatformClassLoader());
        var jni = layer.findModule("org.graalvm.jniutils").orElseThrow();
        Class<?> service = jni.getClassLoader().loadClass("org.graalvm.jniutils.JNIEntryPointProvider");
        assertTrue(jni.canUse(service));
        var providers = ServiceLoader.load(layer, service).stream().map(ServiceLoader.Provider::get).toList();
        assertEquals(Set.of("org.graalvm.jniutils.JNIExceptionWrapperEntryPointsJNIProvider",
                            "org.graalvm.nativebridge.ForeignExceptionEndPointsJNIProvider"),
                providers.stream().map(p -> p.getClass().getName()).collect(Collectors.toSet()));
        for (var provider : providers) {
            String entry = (String) service.getMethod("getClassName").invoke(provider);
            assertNotNull(Class.forName(provider.getClass().getModule(), entry));
        }
    }
}
