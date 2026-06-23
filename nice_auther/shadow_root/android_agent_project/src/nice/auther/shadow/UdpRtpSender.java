package nice.auther.shadow;

import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.util.List;

final class UdpRtpSender implements RtpSender {
    private final DatagramSocket socket;
    private final InetAddress host;
    private final int port;
    private final H264RtpPacketizer packetizer;

    UdpRtpSender(String host, int port, int mtu) throws Exception {
        this.socket = new DatagramSocket();
        this.host = InetAddress.getByName(host);
        this.port = port;
        this.packetizer = new H264RtpPacketizer(mtu);
    }

    @Override
    public void sendAnnexBFrame(byte[] frame, long presentationTimeUs, boolean marker) throws Exception {
        List<RtpPacket> packets = packetizer.packetize(frame, presentationTimeUs, marker);
        for (RtpPacket packet : packets) {
            socket.send(new DatagramPacket(packet.bytes, packet.bytes.length, host, port));
        }
    }

    @Override
    public void close() {
        socket.close();
    }
}
