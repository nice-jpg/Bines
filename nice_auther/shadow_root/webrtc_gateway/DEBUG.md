# nice-webrtc-gateway Debugging

The Go gateway runs on the host connected to the Android device. The Android
agent connects to device-local `127.0.0.1:<rtp-port>`, and `adb reverse` maps
that TCP port back to this host.

## VS Code Launch

Use the `nice-webrtc-gateway` launch configuration. It starts:

```text
signaling: 127.0.0.1:9080
TCP RTP ingest: 127.0.0.1:9081
agent control: 127.0.0.1:9082
events callback: http://127.0.0.1:8080/events
```

When debugging the gateway directly, do not let `start_shadow_session()` start
another gateway on the same ports. Either stop the Python-managed gateway or set
`SHADOW_WEBRTC_GATEWAY_PORT` / `SHADOW_WEBRTC_RTP_PORT` to unused ports.

## Useful Breakpoints

- `Gateway.startTCPForwarder`: confirms the TCP RTP listener starts.
- `Gateway.acceptTCPRTP`: confirms `adb reverse` delivers the Android TCP
  connection to the host.
- `Gateway.forwardTCPRTP`: confirms framed RTP bytes are read and unmarshaled.
- `g.videoTrack.WriteRTP`: confirms RTP packets are passed into WebRTC.
- `Gateway.Answer`: confirms the browser offer creates an answer.

## Headless Delve

Run the VS Code task `dlv nice-webrtc-gateway headless`, then use the
`attach nice-webrtc-gateway` launch configuration.
