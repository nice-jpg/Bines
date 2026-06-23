# nice_auther Android shadow agent

This directory is the Android-side entry point for the `webrtc_h264` backend.
The Python `shadow_root` process expects a built jar/dex artifact passed through
`SHADOW_ANDROID_AGENT_JAR` and starts it with:

```bash
CLASSPATH=/data/local/tmp/nice_shadow_agent.jar app_process / nice.auther.shadow.AgentMain ...
```

The agent contract is intentionally narrow:

- capture the display with a scrcpy-style server path;
- encode H.264 with Android `MediaCodec` hardware encoders;
- send H.264 RTP to the gateway port provided by Python;
- accept IDR/control messages on the configured control port.

`build_android_agent.py` provides the stable build entry point and fails early
with a clear error if `ANDROID_HOME` is not configured.
