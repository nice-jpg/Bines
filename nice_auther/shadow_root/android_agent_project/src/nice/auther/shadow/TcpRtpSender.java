package nice.auther.shadow;

import java.io.BufferedOutputStream;
import java.io.OutputStream;
import java.net.Socket;
import java.util.List;

final class TcpRtpSender implements RtpSender {
    private final Socket socket;
    private final OutputStream output;
    private final H264RtpPacketizer packetizer;
    private int packetCount;
    private int byteCount;

    TcpRtpSender(String host, int port, int mtu) throws Exception {
        System.err.println("nice_shadow_agent tcp connect start host=" + host + " port=" + port);
        this.socket = connectWithRetry(host, port);
        this.socket.setTcpNoDelay(true);
        this.output = new BufferedOutputStream(socket.getOutputStream(), 64 * 1024);
        this.packetizer = new H264RtpPacketizer(mtu);
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
