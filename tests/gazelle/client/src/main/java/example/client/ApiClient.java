package example.client;

import example.api.Message;

public final class ApiClient {
    public static String message() {
        return Message.text();
    }
}
