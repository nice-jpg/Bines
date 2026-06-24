"""Run shadow_root and optionally expose it through an SSH reverse tunnel."""

from __future__ import annotations

import argparse
from dataclasses import replace

from .agent_runner import start_android_agent
from .config import ShadowConfig
from .server import start_shadow_session


def main(argv: list[str] | None = None) -> int:
    config, agent_only = _config_from_args(argv)
    if agent_only:
        start_android_agent(config)
    else:
        start_shadow_session(config=config)
    return 0


def _config_from_args(argv: list[str] | None = None) -> tuple[ShadowConfig, bool]:
    parser = argparse.ArgumentParser(
        description="Start shadow_root and optionally expose it through an SSH reverse tunnel.",
    )
    parser.add_argument("--host", help="Public/remote access host shown in the access URL.")
    parser.add_argument("--bind-host", help="Local address to bind, usually 127.0.0.1 or 0.0.0.0.")
    parser.add_argument("--port", type=int, help="Local shadow_root HTTP port.")
    parser.add_argument("--token", help="Optional browser/API access token.")
    parser.add_argument("--video-backend", choices=["webrtc_h264", "mjpeg_screencap"], help="Display backend.")
    parser.add_argument("--webrtc-gateway-path", help="Path to the external WebRTC gateway binary.")
    parser.add_argument("--webrtc-gateway-unmanaged", action="store_true", help="Do not start/stop the gateway process locally.")
    parser.add_argument("--android-agent-jar", help="Path to the Android H.264 shadow agent jar.")
    parser.add_argument("--android-agent-main-class", help="Android app_process main class.")
    parser.add_argument("--video-max-size", type=int, help="Maximum encoded video dimension.")
    parser.add_argument("--video-fps", type=int, help="Maximum encoded video FPS.")
    parser.add_argument("--video-bitrate", help='Encoded video bitrate, for example "2M" or "900k".')
    parser.add_argument("--video-iframe-interval-ms", type=int, help="H.264 IDR/GOP interval in milliseconds.")
    parser.add_argument("--webrtc-ice-public-ip", help="IP advertised in WebRTC ICE candidates for remote browsers.")
    parser.add_argument("--webrtc-ice-udp-port-min", type=int, help="Minimum UDP port for WebRTC ICE.")
    parser.add_argument("--webrtc-ice-udp-port-max", type=int, help="Maximum UDP port for WebRTC ICE.")
    parser.add_argument("--webrtc-transport", choices=["adb_reverse_tcp", "tcp_direct", "udp_rtp"], help="Android-to-gateway media transport.")
    parser.add_argument("--webrtc-rtp-host", help="Host/IP the Android agent should send RTP to.")
    parser.add_argument("--webrtc-rtp-listen-host", help="Local host/IP the gateway should bind for RTP, usually 0.0.0.0.")
    parser.add_argument("--webrtc-rtp-mtu", type=int, help="RTP packet MTU for Android H.264 sender.")
    parser.add_argument("--android-agent-self-test-rtp", action="store_true", help="Start agent in one-shot synthetic RTP packet test mode.")
    parser.add_argument("--agent-only", action="store_true", help="Only start the local Android agent; the Web UI and gateway run remotely.")
    parser.add_argument("--tunnel", action="store_true", help="Enable SSH reverse tunnel.")
    parser.add_argument("--tunnel-ssh-host", help="SSH target, for example user@example.com.")
    parser.add_argument("--tunnel-ssh-port", type=int, help="SSH port. Defaults to 22.")
    parser.add_argument("--tunnel-ssh-key", help="Path to SSH private key.")
    parser.add_argument("--tunnel-remote-bind-host", help="Remote bind host for ssh -R, usually 0.0.0.0.")
    parser.add_argument("--tunnel-remote-port", type=int, help="Remote public port. Defaults to --port.")
    parser.add_argument("--tunnel-local-host", help="Local host reached by the tunnel. Defaults to 127.0.0.1.")
    parser.add_argument("--tunnel-extra-args", help='Extra ssh args, for example "-o StrictHostKeyChecking=no".')
    args = parser.parse_args(argv)

    config = ShadowConfig.from_env()
    overrides = {
        "host": args.host,
        "bind_host": args.bind_host,
        "port": args.port,
        "token": args.token,
        "video_backend": args.video_backend,
        "webrtc_gateway_path": args.webrtc_gateway_path,
        "android_agent_jar": args.android_agent_jar,
        "android_agent_main_class": args.android_agent_main_class,
        "video_max_size": args.video_max_size,
        "video_fps": args.video_fps,
        "video_bitrate": args.video_bitrate,
        "video_iframe_interval_ms": args.video_iframe_interval_ms,
        "webrtc_ice_public_ip": args.webrtc_ice_public_ip,
        "webrtc_ice_udp_port_min": args.webrtc_ice_udp_port_min,
        "webrtc_ice_udp_port_max": args.webrtc_ice_udp_port_max,
        "webrtc_transport": args.webrtc_transport,
        "webrtc_rtp_host": args.webrtc_rtp_host,
        "webrtc_rtp_listen_host": args.webrtc_rtp_listen_host,
        "webrtc_rtp_mtu": args.webrtc_rtp_mtu,
        "tunnel_ssh_host": args.tunnel_ssh_host,
        "tunnel_ssh_port": args.tunnel_ssh_port,
        "tunnel_ssh_key": args.tunnel_ssh_key,
        "tunnel_remote_bind_host": args.tunnel_remote_bind_host,
        "tunnel_remote_port": args.tunnel_remote_port,
        "tunnel_local_host": args.tunnel_local_host,
        "tunnel_extra_args": args.tunnel_extra_args,
    }
    if args.webrtc_gateway_unmanaged:
        overrides["webrtc_gateway_managed"] = False
    if args.tunnel:
        overrides["tunnel_enabled"] = True
    if args.android_agent_self_test_rtp:
        overrides["android_agent_self_test_rtp"] = True
    return replace(config, **{key: value for key, value in overrides.items() if value is not None}), args.agent_only


if __name__ == "__main__":
    raise SystemExit(main())
