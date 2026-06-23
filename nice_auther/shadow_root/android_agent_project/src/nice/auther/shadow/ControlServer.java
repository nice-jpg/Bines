package nice.auther.shadow;

import java.io.Closeable;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.atomic.AtomicBoolean;

final class ControlServer implements Closeable {
    private final DatagramSocket socket;
    private final AtomicBoolean idrRequested = new AtomicBoolean(false);
    private final Thread thread;
    private volatile boolean running = true;

    ControlServer(int port) throws Exception {
        DatagramSocket createdSocket;
        try {
            createdSocket = new DatagramSocket(port);
            System.err.println("nice_shadow_agent control listening port=" + port);
        } catch (Exception exc) {
            createdSocket = null;
            System.err.println("nice_shadow_agent control disabled port=" + port + " error=" + exc);
        }
        this.socket = createdSocket;
        this.thread = new Thread(new Runnable() {
            @Override
            public void run() {
                loop();
            }
        }, "nice-shadow-control");
        this.thread.setDaemon(true);
    }

    void start() {
        if (socket != null) {
            thread.start();
        }
    }

    boolean consumeIdrRequest() {
        return idrRequested.getAndSet(false);
    }

    private void loop() {
        byte[] buffer = new byte[256];
        while (running) {
            try {
                if (socket == null) {
                    return;
                }
                DatagramPacket packet = new DatagramPacket(buffer, buffer.length);
                socket.receive(packet);
                String message = new String(packet.getData(), packet.getOffset(), packet.getLength(), StandardCharsets.UTF_8).trim();
                if ("IDR".equalsIgnoreCase(message) || "PLI".equalsIgnoreCase(message)) {
                    idrRequested.set(true);
                }
            } catch (Exception ignored) {
                if (running) {
                    idrRequested.set(true);
                }
            }
        }
    }

    @Override
    public void close() {
        running = false;
        if (socket != null) {
            socket.close();
        }
    }
}
