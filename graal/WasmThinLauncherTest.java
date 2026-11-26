package graal;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.HexFormat;
import org.junit.Test;

public class WasmThinLauncherTest {
    @Test
    public void runsGuestWithTheResolvedStandaloneModules() throws Exception {
        Path root = Files.createTempDirectory(Path.of(System.getenv("TEST_TMPDIR")), "wasm");
        String install = System.getenv("WASM_INSTALL");
        Path launcher;
        if (install == null) {
            launcher = Files.createDirectories(root.resolve("bin")).resolve("wasm");
            Files.copy(Path.of(System.getenv("WASM_LAUNCHER")), launcher);
            launcher.toFile().setExecutable(true);
            Files.createSymbolicLink(root.resolve("jvm"), Path.of(System.getProperty("java.home")));
            Path modules = Files.createDirectory(root.resolve("modules"));
            for (String jar : System.getenv("MODULE_JARS").split(" ")) {
                Path source = Path.of(jar);
                Files.createSymbolicLink(modules.resolve(source.getFileName()), source.toAbsolutePath());
            }
        } else {
            Path distribution = Path.of(install);
            launcher = distribution.resolve("bin/wasm");
            assertTrue(Files.isRegularFile(distribution.resolve("LICENSE.txt")));
            assertTrue(Files.isRegularFile(distribution.resolve("3rd_party_licenses.txt")));
            assertTrue(Files.readString(distribution.resolve("release")).contains("JAVA_VERSION="));
            assertTrue(Files.isRegularFile(distribution.resolve("native-image.properties")));
        }
        // (module (func (export "_start")))
        Path guest = root.resolve("hello.wasm");
        Files.write(guest, HexFormat.of().parseHex(
                "0061736d0100000001040160000003020100070a01065f737461727400000a040102000b"));
        Process process = new ProcessBuilder(launcher.toString(), guest.toString())
                .redirectErrorStream(true).start();
        String output = new String(process.getInputStream().readAllBytes(), StandardCharsets.UTF_8);
        assertEquals(output, 0, process.waitFor());
    }
}
