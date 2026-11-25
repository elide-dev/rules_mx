package graal;

import com.oracle.truffle.api.Truffle;
import com.oracle.truffle.api.impl.DefaultTruffleRuntime;
import com.oracle.truffle.api.strings.TruffleString;
import com.oracle.truffle.runtime.OptimizedCallTarget;
import com.oracle.truffle.runtime.hotspot.HotSpotTruffleRuntime;
import com.oracle.truffle.sl.runtime.SLContext;
import java.nio.file.Files;
import java.nio.file.Path;
import org.graalvm.polyglot.Context;

public final class CompiledGuest {
    public static void main(String[] args) throws Exception {
        var compiler = ModuleLayer.boot().configuration().findModule("jdk.graal.compiler").orElseThrow();
        var location = compiler.reference().location().orElseThrow();
        if (!"file".equals(location.getScheme()) || !Files.isSameFile(Path.of(args[0]), Path.of(location))) {
            throw new AssertionError("Unexpected optimizing compiler: " + location);
        }
        if (!(Truffle.getRuntime() instanceof HotSpotTruffleRuntime)) {
            var runtime = Truffle.getRuntime();
            throw new AssertionError(runtime instanceof DefaultTruffleRuntime ? ((DefaultTruffleRuntime) runtime).getFallbackReason() : runtime);
        }
        for (String bytecode : new String[]{"false", "true"}) {
            var builder = Context.newBuilder("sl").allowExperimentalOptions(true)
                    .option("engine.BackgroundCompilation", "false")
                    .option("sl.UseBytecode", bytecode);
            if (bytecode.equals("true")) {
                builder.option("sl.ForceBytecodeTier", "CACHED");
            }
            try (var context = builder.build()) {
                context.eval("sl", "function twice(x) { return x + x; }");
                var twice = context.getBindings("sl").getMember("twice");
                if (twice.execute(10).asInt() != 20) {
                    throw new AssertionError("Incorrect warmup result");
                }
                context.enter();
                try {
                    var name = TruffleString.fromJavaStringUncached("twice", TruffleString.Encoding.UTF_16);
                    var function = SLContext.get(null).getFunctionRegistry().lookup(name, false);
                    var target = (OptimizedCallTarget) function.getCallTarget();
                    target.compile(true);
                    target.waitForCompilation();
                    if (!target.isValidLastTier()) {
                        throw new AssertionError("No installed last-tier code for " + target);
                    }
                    if (twice.execute(21).asInt() != 42 || !target.isValidLastTier()) {
                        throw new AssertionError("Incorrect result or deoptimization with sl.UseBytecode=" + bytecode);
                    }
                } finally {
                    context.leave();
                }
            }
        }
    }
}
