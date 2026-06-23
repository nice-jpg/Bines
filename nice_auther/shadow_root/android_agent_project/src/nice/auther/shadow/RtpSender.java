package nice.auther.shadow;

import java.io.Closeable;

interface RtpSender extends Closeable {
    void sendAnnexBFrame(byte[] frame, long presentationTimeUs, boolean marker) throws Exception;
}
