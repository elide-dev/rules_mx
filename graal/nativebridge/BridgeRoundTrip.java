// The upstream benchmark's generated factories and service types have package visibility.
package org.graalvm.nativebridge.benchmark;

import java.nio.file.Path;
import java.util.List;
import org.graalvm.nativebridge.ProcessIsolateConfig;

public final class BridgeRoundTrip {
    public static void main(String[] args) throws Exception {
        Path launcher = Path.of(args[0]);
        checkService(BenchmarkFactory.createNonIsolated());
        if (args.length == 2) {
            try (Runner internal = new InternalIsolationRunner(new Runner.Config(Path.of(args[1]), launcher))) {
                checkService(internal.getService());
            }
        }
        Path socket = ProcessIsolateConfig.createDefaultInitiatorSocketAddress(null);
        var config = ProcessIsolateConfig.newInitiatorBuilder(launcher, socket);
        if (args.length == 2) {
            config.launcherArgument(args[1]);
        } else {
            Path vm = Path.of(System.getProperty("java.home"), "lib", "server", System.mapLibraryName("jvm"));
            config.launcherArguments(List.of("-Djava.class.path=" + System.getProperty("java.class.path"), "--", vm.toString()));
        }
        config.launcherArguments(List.of(ProcessIsolate.class.getName(), socket.toString()));
        ForeignService service = BenchmarkFactory.createExternallyIsolated(config.build());
        try {
            checkService(service);
        } finally {
            service.getPeer().getIsolate().shutdown();
        }
    }

    private static void checkService(Service service) {
        service.noop();
        Point point = new Point(4, 5, 6);
        if (service.primitives(42, 7) != 42 || !"bridge".equals(service.strings("", "bridge"))
                || service.references(service, BenchmarkFactory.createNonIsolated()).primitives(19, 0) != 19
                || !point.equals(service.marshalled(point, new Point(0, 0, 0)))
                || !List.of(point).equals(service.marshalled2(List.of(point), List.of()))) {
            throw new AssertionError("Unexpected reply from the process isolate");
        }
    }
}
