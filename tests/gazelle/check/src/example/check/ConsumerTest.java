package example.check;

import example.app.Consumer;
import example.client.ApiClient;
import org.junit.Test;
import static org.junit.Assert.assertEquals;

public class ConsumerTest {
    @Test
    public void exposesMessage() {
        assertEquals("mx", new Consumer().message());
        assertEquals("mx", ApiClient.message());
    }
}
