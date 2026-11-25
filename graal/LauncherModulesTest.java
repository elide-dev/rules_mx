package graal;

import static org.junit.Assert.*;

import java.io.IOException;
import java.lang.module.ModuleFinder;
import java.lang.reflect.InvocationTargetException;
import java.nio.file.Path;
import java.util.List;
import java.util.Set;
import java.util.jar.JarFile;
import org.junit.Test;

public class LauncherModulesTest {
    @Test
    public void resolvesLauncherAndSelectsJlineProviderForRunningJdk() throws Exception {
        Path[] jars = List.of("WORD", "COLLECTIONS", "NATIVEIMAGE", "POLYGLOT", "JLINE3", "LAUNCHER_COMMON")
                .stream().map(name -> Path.of(System.getenv(name))).toArray(Path[]::new);
        var boot = ModuleLayer.boot();
        var config = boot.configuration().resolve(ModuleFinder.of(jars), ModuleFinder.of(),
                Set.of("org.graalvm.launcher"));
        var controller = ModuleLayer.defineModulesWithOneLoader(config, List.of(boot), ClassLoader.getPlatformClassLoader());
        var layer = controller.layer();
        var launcher = layer.findModule("org.graalvm.launcher").orElseThrow();
        var jline = layer.findModule("org.graalvm.shadowed.jline").orElseThrow();
        assertTrue(launcher.canRead(jline));
        assertNotNull(Class.forName(launcher, "org.graalvm.launcher.Launcher"));

        String prefix = "org.graalvm.shadowed.org.jline.";
        Class<?> provider = Class.forName(jline, prefix + "terminal.spi.TerminalProvider");
        var load = provider.getMethod("load", String.class);
        assertEquals(prefix + "terminal.impl.exec.ExecTerminalProvider", load.invoke(null, "exec").getClass().getName());
        if (Runtime.version().feature() >= 22) {
            // The JDK 22 API grants native access to a module in a child layer.
            ModuleLayer.Controller.class.getMethod("enableNativeAccess", Module.class).invoke(controller, jline);
            assertEquals(prefix + "terminal.impl.ffm.FfmTerminalProvider", load.invoke(null, "ffm").getClass().getName());
        } else {
            var error = assertThrows(InvocationTargetException.class, () -> load.invoke(null, "ffm"));
            assertTrue(error.getCause() instanceof IOException);
        }
        try (var jar = new JarFile(System.getenv("JLINE3"))) {
            assertTrue(jar.isMultiRelease());
            assertNotNull(jar.getJarEntry("META-INF/versions/22/module-info.class"));
            assertNotNull(jar.getJarEntry(prefix.replace('.', '/') + "utils/xterm.caps"));
        }
    }
}
