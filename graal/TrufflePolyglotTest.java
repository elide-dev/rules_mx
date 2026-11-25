// The generated factory has package visibility in the upstream Polyglot project.
package com.oracle.truffle.polyglot.isolate;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotEquals;
import static org.junit.Assert.assertThrows;

import java.util.ServiceLoader;
import org.graalvm.polyglot.impl.AbstractPolyglotImpl;
import org.junit.Test;

public class TrufflePolyglotTest {
    @Test
    public void generatedFactoryDistinguishesContextAndFileSystemServices() {
        assertNotEquals(PolyglotIsolateForeignFactoryGen.lookupServiceId(ForeignContextDispatch.class),
                        PolyglotIsolateForeignFactoryGen.lookupServiceId(ForeignFileSystem.class));
        assertThrows(IllegalArgumentException.class, () -> PolyglotIsolateForeignFactoryGen.lookupServiceId(String.class));
    }

    @Test
    public void discoversTruffleImplementationAndReadsPackagedVersion() {
        var provider = ServiceLoader.load(AbstractPolyglotImpl.class).findFirst().orElseThrow();
        assertEquals("com.oracle.truffle.polyglot.PolyglotImpl", provider.getClass().getName());
        assertEquals("25.5.5", provider.getTruffleVersion());
    }
}
