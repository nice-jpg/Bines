package nice.auther.shadow;

import android.media.MediaCodec;
import android.media.MediaCodecInfo;
import android.media.MediaFormat;
import android.os.Bundle;
import android.view.Surface;

import java.io.Closeable;
import java.nio.ByteBuffer;

final class H264SurfaceEncoder implements Closeable {
    interface FrameSink {
        void onFrame(byte[] annexBFrame, long presentationTimeUs, boolean marker) throws Exception;
    }

    private static final long DEQUEUE_TIMEOUT_US = 10000L;

    private final AgentConfig config;
    private final FrameSink frameSink;
    private MediaCodec encoder;
    private Surface inputSurface;
    private Thread drainThread;
    private volatile boolean running;
    private int frameCount;
    private byte[] codecConfig;

    H264SurfaceEncoder(AgentConfig config, FrameSink frameSink) {
        this.config = config;
        this.frameSink = frameSink;
    }

    Surface start(int width, int height) throws Exception {
        MediaFormat format = MediaFormat.createVideoFormat(MediaFormat.MIMETYPE_VIDEO_AVC, width, height);
        format.setInteger(MediaFormat.KEY_COLOR_FORMAT, MediaCodecInfo.CodecCapabilities.COLOR_FormatSurface);
        format.setInteger(MediaFormat.KEY_FRAME_RATE, config.fps);
        format.setInteger(MediaFormat.KEY_BIT_RATE, config.bitrateBitsPerSecond());
        format.setInteger(MediaFormat.KEY_I_FRAME_INTERVAL, Math.max(1, config.iFrameIntervalMs / 1000));
        forceBaselineProfile(format);
        encoder = MediaCodec.createEncoderByType(MediaFormat.MIMETYPE_VIDEO_AVC);
        encoder.configure(format, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE);
        inputSurface = encoder.createInputSurface();
        encoder.start();
        System.err.println("nice_shadow_agent encoder started width=" + width + " height=" + height + " fps=" + config.fps + " bitrate=" + config.bitrateBitsPerSecond());
        running = true;
        drainThread = new Thread(new Runnable() {
            @Override
            public void run() {
                drainLoop();
            }
        }, "nice-shadow-h264-drain");
        drainThread.start();
        return inputSurface;
    }

    private void forceBaselineProfile(MediaFormat format) {
        try {
            format.setInteger(MediaFormat.KEY_PROFILE, MediaCodecInfo.CodecProfileLevel.AVCProfileBaseline);
            format.setInteger(MediaFormat.KEY_LEVEL, MediaCodecInfo.CodecProfileLevel.AVCLevel31);
            System.err.println("nice_shadow_agent encoder profile baseline level=31");
        } catch (Exception exc) {
            System.err.println("nice_shadow_agent encoder profile baseline unavailable " + exc);
        }
    }

    void requestKeyFrame() {
        if (encoder == null) {
            return;
        }
        Bundle params = new Bundle();
        params.putInt(MediaCodec.PARAMETER_KEY_REQUEST_SYNC_FRAME, 0);
        encoder.setParameters(params);
    }

    private void drainLoop() {
        MediaCodec.BufferInfo info = new MediaCodec.BufferInfo();
        while (running) {
            int index;
            try {
                index = encoder.dequeueOutputBuffer(info, DEQUEUE_TIMEOUT_US);
            } catch (Exception ignored) {
                continue;
            }
            if (index < 0) {
                continue;
            }
            try {
                ByteBuffer buffer = encoder.getOutputBuffer(index);
                if (buffer != null && info.size > 0) {
                    byte[] frame = new byte[info.size];
                    buffer.position(info.offset);
                    buffer.limit(info.offset + info.size);
                    buffer.get(frame);
                    boolean codecConfigBuffer = (info.flags & MediaCodec.BUFFER_FLAG_CODEC_CONFIG) != 0;
                    if (codecConfigBuffer) {
                        codecConfig = frame;
                        System.err.println("nice_shadow_agent encoder codec_config bytes=" + frame.length);
                        continue;
                    }
                    boolean keyFrame = (info.flags & MediaCodec.BUFFER_FLAG_KEY_FRAME) != 0;
                    if (keyFrame && codecConfig != null) {
                        frame = H264AnnexB.concat(codecConfig, frame);
                    }
                    frameCount++;
                    if (frameCount == 1 || frameCount == 30 || frameCount % 300 == 0) {
                        System.err.println("nice_shadow_agent encoder frame count=" + frameCount + " bytes=" + frame.length + " pts=" + info.presentationTimeUs + " key=" + keyFrame);
                    }
                    frameSink.onFrame(frame, info.presentationTimeUs, true);
                }
            } catch (Exception exc) {
                System.err.println("nice_shadow_agent encoder sink error " + exc);
            } finally {
                encoder.releaseOutputBuffer(index, false);
            }
        }
    }

    @Override
    public void close() {
        running = false;
        if (drainThread != null) {
            try {
                drainThread.join(500);
            } catch (InterruptedException ignored) {
                Thread.currentThread().interrupt();
            }
        }
        if (encoder != null) {
            try {
                encoder.stop();
            } catch (Exception ignored) {
            }
            encoder.release();
        }
        if (inputSurface != null) {
            inputSurface.release();
        }
    }
}
