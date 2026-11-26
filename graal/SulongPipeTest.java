package graal;

import static org.junit.Assert.assertEquals;

import com.oracle.truffle.llvm.tests.pipe.CaptureNativeOutput;
import org.junit.Test;

public class SulongPipeTest {
    @Test
    public void capturesBothFileDescriptorsThroughTheCompiledJniLibrary() throws Exception {
        try (var capture = new CaptureNativeOutput()) {
            System.out.print("captured stdout");
            System.err.print("captured stderr");
            CaptureNativeOutput.flushStdFiles();
            assertEquals("captured stdout", capture.getStdOut());
            assertEquals("captured stderr", capture.getStdErr());
        }
    }
}
