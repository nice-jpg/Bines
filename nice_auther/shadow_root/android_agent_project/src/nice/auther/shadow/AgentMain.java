package nice.auther.shadow;

import android.view.Surface;

public final class AgentMain {
    private AgentMain() {
    }

    public static void main(String[] args) throws Exception {
        AgentConfig config = AgentConfig.parse(args);
        System.err.println("nice_shadow_agent start transport=" + config.transport + " rtpHost=" + config.rtpHost + " rtpPort=" + config.rtpPort + " fps=" + config.fps + " maxSize=" + config.maxSize + " bitrate=" + config.bitrate);
        final RtpSender[] senderRef = new RtpSender[1];
        try {
            if (config.selfTestRtp) {
                senderRef[0] = createSender(config, null);
                RtpSender sender = senderRef[0];
                sendSelfTestFrame(sender);
                return;
            }

            DisplayMirror.DisplayInfo displayInfo = DisplayMirror.mainDisplayInfo();
            DisplayMirror.EncodedSize encodedSize = DisplayMirror.encodedSize(displayInfo, config.maxSize);
            System.err.println("nice_shadow_agent display width=" + displayInfo.width + " height=" + displayInfo.height + " encodedWidth=" + encodedSize.width + " encodedHeight=" + encodedSize.height);
            RemoteControlHandler controlHandler = new RemoteControlHandler(new RemoteInputInjector(displayInfo.width, displayInfo.height));
            ControlServer controlServer = new ControlServer(config.controlPort, controlHandler);
            controlServer.start();
            senderRef[0] = createSender(config, controlHandler);
            H264SurfaceEncoder encoder = new H264SurfaceEncoder(config, new H264SurfaceEncoder.FrameSink() {
                @Override
                public void onFrame(byte[] annexBFrame, long presentationTimeUs, boolean marker) throws Exception {
                    senderRef[0].sendAnnexBFrame(annexBFrame, presentationTimeUs, marker);
                }
            });
            Surface inputSurface = encoder.start(encodedSize.width, encodedSize.height);
            DisplayMirror mirror = DisplayMirror.create(inputSurface, displayInfo, encodedSize);
            try {
                while (true) {
                    if (controlHandler.consumeIdrRequest()) {
                        System.err.println("nice_shadow_agent request key frame");
                        encoder.requestKeyFrame();
                    }
                    Thread.sleep(50L);
                    if (!inputSurface.isValid()) {
                        System.err.println("nice_shadow_agent input surface invalid");
                        break;
                    }
                }
            } finally {
                mirror.close();
                encoder.close();
                controlServer.close();
            }
        } catch (Throwable throwable) {
            System.err.println("nice_shadow_agent fatal " + throwable);
            throwable.printStackTrace(System.err);
            throw throwable;
        } finally {
            if (senderRef[0] != null) {
                senderRef[0].close();
            }
        }
    }

    private static RtpSender createSender(AgentConfig config, RemoteControlHandler controlHandler) throws Exception {
        if ("udp_rtp".equals(config.transport)) {
            return new UdpRtpSender(config.rtpHost, config.rtpPort, config.mtu);
        }
        return new TcpRtpSender(config.rtpHost, config.rtpPort, config.mtu, controlHandler);
    }

    private static void sendSelfTestFrame(RtpSender sender) throws Exception {
        byte[] frame = new byte[]{
                0, 0, 0, 1, 0x67, 0x42, 0x00, 0x1e, (byte) 0x95, (byte) 0xa8, 0x14, 0x01, 0x6e, (byte) 0x9b,
                0, 0, 0, 1, 0x68, (byte) 0xce, 0x06, (byte) 0xe2,
                0, 0, 0, 1, 0x65, (byte) 0x88, (byte) 0x84, 0x21, (byte) 0xa0
        };
        sender.sendAnnexBFrame(frame, 0, true);
    }
}
