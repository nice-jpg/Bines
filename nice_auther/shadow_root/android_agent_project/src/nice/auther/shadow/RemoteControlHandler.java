package nice.auther.shadow;

import java.util.concurrent.atomic.AtomicBoolean;

final class RemoteControlHandler {
    private final AtomicBoolean idrRequested = new AtomicBoolean(false);
    private final RemoteInputInjector inputInjector;

    RemoteControlHandler(RemoteInputInjector inputInjector) {
        this.inputInjector = inputInjector;
    }

    void requestIdr() {
        idrRequested.set(true);
    }

    boolean consumeIdrRequest() {
        return idrRequested.getAndSet(false);
    }

    void handleMessage(String message) {
        String normalized = message == null ? "" : message.trim();
        if (normalized.length() == 0) {
            return;
        }
        if ("PLI".equalsIgnoreCase(normalized) || "IDR".equalsIgnoreCase(normalized)) {
            requestIdr();
            return;
        }
        try {
            inputInjector.handleJson(normalized);
        } catch (Exception exc) {
            System.err.println("nice_shadow_agent control error " + exc + " message=" + normalized);
        }
    }
}
