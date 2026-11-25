package graal;

import static org.junit.Assert.*;

import java.lang.module.ModuleFinder;
import java.nio.file.Path;
import java.util.Arrays;
import java.util.List;
import java.util.ServiceLoader;
import java.util.Set;
import org.junit.Test;

public class TruffleRuntimeModulesTest {
    @Test
    public void resolvesHotSpotProviderAndCompilerInterface() throws Exception {
        Path[] jars = Arrays.stream(System.getenv("MODULE_JARS").split(" ")).map(Path::of).toArray(Path[]::new);
        var boot = ModuleLayer.boot();
        var config = boot.configuration().resolve(ModuleFinder.of(jars), ModuleFinder.of(), Set.of("org.graalvm.truffle.runtime"));
        var layer = ModuleLayer.defineModulesWithOneLoader(config, List.of(boot), ClassLoader.getPlatformClassLoader()).layer();
        var runtime = layer.findModule("org.graalvm.truffle.runtime").orElseThrow();
        var compiler = layer.findModule("org.graalvm.truffle.compiler").orElseThrow();
        assertTrue(runtime.canRead(compiler));
        assertTrue(compiler.isExported("com.oracle.truffle.compiler.hotspot", runtime));
        var api = layer.findModule("org.graalvm.truffle").orElseThrow();
        var service = Class.forName(api, "com.oracle.truffle.api.TruffleRuntimeAccess");
        var access = ServiceLoader.load(layer, service).findFirst().orElseThrow();
        assertEquals("com.oracle.truffle.runtime.hotspot.HotSpotTruffleRuntimeAccess", access.getClass().getName());
        assertSame(runtime, access.getClass().getModule());
    }
}
