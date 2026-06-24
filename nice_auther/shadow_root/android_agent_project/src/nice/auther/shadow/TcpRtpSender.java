package nice.auther.shadow;

import java.io.BufferedOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.Socket;
import java.util.List;

final class TcpRtpSender implements RtpSender {
    private final Socket socket;
    private final OutputStream output;
    private final RemoteControlHandler controlHandler;
    private final H264RtpPacketizer packetizer;
    private final Thread controlThread;
    private int packetCount;
    private int byteCount;

    TcpRtpSender(String host, int port, int mtu, RemoteControlHandler controlHandler) throws Exception {
        System.err.println("nice_shadow_agent tcp connect start host=" + host + " port=" + port);
        this.socket = connectWithRetry(host, port);
        this.socket.setTcpNoDelay(true);
        this.output = new BufferedOutputStream(socket.getOutputStream(), 64 * 1024);
        this.controlHandler = controlHandler;
        this.packetizer = new H264RtpPacketizer(mtu);
        this.controlThread = new Thread(new Runnable() {
            @Override
            public void run() {
                readControlLoop();
            }
        }, "nice-shadow-tcp-control");
        this.controlThread.setDaemon(true);
        if (controlHandler != null) {
            this.controlThread.start();
        }
        System.err.println("nice_shadow_agent tcp connected local=" + socket.getLocalSocketAddress() + " remote=" + socket.getRemoteSocketAddress());
    }

    private static Socket connectWithRetry(String host, int port) throws Exception {
        Exception last = null;
        for (int i = 0; i < 30; i++) {
            try {
                return new Socket(host, port);
            } catch (Exception exc) {
                last = exc;
                Thread.sleep(100L);
            }
        }
        throw last;
    }

    private void readControlLoop() {
        byte[] header = new byte[2];
        try {
            InputStream input = socket.getInputStream();
            while (!socket.isClosed()) {
                readFully(input, header, 0, header.length);
                int length = ((header[0] & 0xff) << 8) | (header[1] & 0xff);
                if (length <= 0 || length > 65535) {
                    continue;
                }
                byte[] payload = new byte[length];
                readFully(input, payload, 0, payload.length);
                controlHandler.handleMessage(new String(payload, "UTF-8"));
            }
        } catch (Exception exc) {
            if (!socket.isClosed()) {
                System.err.println("nice_shadow_agent tcp control end " + exc);
            }
        }
    }

    private static void readFully(InputStream input, byte[] buffer, int offset, int length) throws Exception {
        int done = 0;
        while (done < length) {
            int read = input.read(buffer, offset + done, length - done);
            if (read < 0) {
                throw new java.io.EOFException();
            }
            done += read;
        }
    }

    @Override
    public void sendAnnexBFrame(byte[] frame, long presentationTimeUs, boolean marker) throws Exception {
        List<RtpPacket> packets = packetizer.packetize(frame, presentationTimeUs, marker);
        for (RtpPacket packet : packets) {
            int length = packet.bytes.length;
            if (length > 0xffff) {
                continue;
            }
            output.write((length >>> 8) & 0xff);
            output.write(length & 0xff);
            output.write(packet.bytes);
            packetCount++;
            byteCount += length;
            if (packetCount <= 5 || packetCount == 30 || packetCount % 300 == 0) {
                System.err.println("nice_shadow_agent tcp packet count=" + packetCount + " length=" + length + " head=" + hexPrefix(packet.bytes, 16));
            }
        }
        output.flush();
        if (packetCount == packets.size() || packetCount == 30 || packetCount % 300 == 0) {
            System.err.println("nice_shadow_agent tcp sent packets=" + packetCount + " bytes=" + byteCount + " frame_bytes=" + frame.length);
        }
    }

    private static String hexPrefix(byte[] data, int limit) {
        StringBuilder builder = new StringBuilder();
        int end = Math.min(data.length, limit);
        for (int i = 0; i < end; i++) {
            if (i > 0) {
                builder.append(' ');
            }
            int value = data[i] & 0xff;
            if (value < 16) {
                builder.append('0');
            }
            builder.append(Integer.toHexString(value));
        }
        return builder.toString();
    }

    @Override
    public void close() {
        try {
            output.close();
        } catch (Exception ignored) {
        }
        try {
            socket.close();
        } catch (Exception ignored) {
        }
    }
}
