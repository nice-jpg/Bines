package nice.auther.shadow;

import java.util.ArrayList;
import java.util.List;
import java.util.Random;

final class H264RtpPacketizer {
    private static final int RTP_HEADER_SIZE = 12;
    private static final int H264_CLOCK_RATE = 90000;

    private final int mtu;
    private final int ssrc;
    private int sequenceNumber;
    private int accessUnitCount;

    H264RtpPacketizer(int mtu) {
        this.mtu = mtu;
        Random random = new Random();
        this.ssrc = random.nextInt();
        this.sequenceNumber = random.nextInt() & 0xffff;
    }

    List<RtpPacket> packetize(byte[] annexBFrame, long presentationTimeUs, boolean markerOnLastPacket) {
        List<byte[]> nalUnits = H264AnnexB.splitNalUnits(annexBFrame, 0, annexBFrame.length);
        accessUnitCount++;
        if (accessUnitCount <= 5 || accessUnitCount == 30 || accessUnitCount % 300 == 0) {
            System.err.println("nice_shadow_agent rtp access_unit=" + accessUnitCount + " nals=" + nalSummary(nalUnits) + " marker=" + markerOnLastPacket);
        }
        List<RtpPacket> packets = new ArrayList<>();
        long timestamp = presentationTimeUs * H264_CLOCK_RATE / 1000000L;
        for (int i = 0; i < nalUnits.size(); i++) {
            boolean lastNal = i == nalUnits.size() - 1;
            packets.addAll(packetizeNal(nalUnits.get(i), timestamp, markerOnLastPacket && lastNal));
        }
        return packets;
    }

    private List<RtpPacket> packetizeNal(byte[] nal, long timestamp, boolean markerOnLastPacket) {
        List<RtpPacket> packets = new ArrayList<>();
        int maxSingleNalPayload = mtu - RTP_HEADER_SIZE;
        if (nal.length <= maxSingleNalPayload) {
            byte[] packet = new byte[RTP_HEADER_SIZE + nal.length];
            writeHeader(packet, timestamp, markerOnLastPacket);
            System.arraycopy(nal, 0, packet, RTP_HEADER_SIZE, nal.length);
            logInvalidPacket(packet);
            packets.add(new RtpPacket(packet));
            return packets;
        }

        // FU-A fragmentation for NAL units larger than one RTP payload.
        int maxFuPayload = mtu - RTP_HEADER_SIZE - 2;
        byte nalHeader = nal[0];
        byte fuIndicator = (byte) ((nalHeader & 0xe0) | 28);
        byte nalType = (byte) (nalHeader & 0x1f);
        int offset = 1;
        boolean first = true;
        while (offset < nal.length) {
            int chunk = Math.min(maxFuPayload, nal.length - offset);
            boolean last = offset + chunk >= nal.length;
            byte[] packet = new byte[RTP_HEADER_SIZE + 2 + chunk];
            writeHeader(packet, timestamp, markerOnLastPacket && last);
            packet[RTP_HEADER_SIZE] = fuIndicator;
            packet[RTP_HEADER_SIZE + 1] = (byte) ((first ? 0x80 : 0) | (last ? 0x40 : 0) | nalType);
            System.arraycopy(nal, offset, packet, RTP_HEADER_SIZE + 2, chunk);
            logInvalidPacket(packet);
            packets.add(new RtpPacket(packet));
            offset += chunk;
            first = false;
        }
        return packets;
    }

    private void logInvalidPacket(byte[] packet) {
        if (packet.length < RTP_HEADER_SIZE || (packet[0] & 0xff) != 0x80) {
            System.err.println("nice_shadow_agent invalid rtp head=" + (packet.length > 0 ? (packet[0] & 0xff) : -1));
        }
    }

    private void writeHeader(byte[] packet, long timestamp, boolean marker) {
        packet[0] = (byte) 0x80;
        packet[1] = (byte) ((marker ? 0x80 : 0) | 96);
        packet[2] = (byte) ((sequenceNumber >> 8) & 0xff);
        packet[3] = (byte) (sequenceNumber & 0xff);
        sequenceNumber = (sequenceNumber + 1) & 0xffff;
        packet[4] = (byte) ((timestamp >> 24) & 0xff);
        packet[5] = (byte) ((timestamp >> 16) & 0xff);
        packet[6] = (byte) ((timestamp >> 8) & 0xff);
        packet[7] = (byte) (timestamp & 0xff);
        packet[8] = (byte) ((ssrc >> 24) & 0xff);
        packet[9] = (byte) ((ssrc >> 16) & 0xff);
        packet[10] = (byte) ((ssrc >> 8) & 0xff);
        packet[11] = (byte) (ssrc & 0xff);
    }

    private String nalSummary(List<byte[]> nalUnits) {
        StringBuilder builder = new StringBuilder();
        for (int i = 0; i < nalUnits.size(); i++) {
            if (i > 0) {
                builder.append(",");
            }
            byte[] nal = nalUnits.get(i);
            int type = nal.length > 0 ? nal[0] & 0x1f : -1;
            builder.append(type).append(":").append(nal.length);
        }
        return builder.toString();
    }
}
