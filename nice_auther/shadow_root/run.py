"""Run shadow_root and optionally expose it through an SSH reverse tunnel."""

from __future__ import annotations

import argparse
from dataclasses import replace

from .config import ShadowConfig
from .server import start_shadow_session


def main(argv: list[str] | None = None) -> int:
    start_shadow_session(config=_config_from_args(argv))
    return 0


def _config_from_args(argv: list[str] | None = None) -> ShadowConfig:
    parser = argparse.ArgumentParser(
        description="Start shadow_root and optionally expose it through an SSH reverse tunnel.",
    )
    parser.add_argument("--host", help="Public/remote access host shown in the access URL.")
    parser.add_argument("--bind-host", help="Local address to bind, usually 127.0.0.1 or 0.0.0.0.")
    parser.add_argument("--port", type=int, help="Local shadow_root HTTP port.")
    parser.add_argument("--token", help="Optional browser/API access token.")
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
        "tunnel_ssh_host": args.tunnel_ssh_host,
        "tunnel_ssh_port": args.tunnel_ssh_port,
        "tunnel_ssh_key": args.tunnel_ssh_key,
        "tunnel_remote_bind_host": args.tunnel_remote_bind_host,
        "tunnel_remote_port": args.tunnel_remote_port,
        "tunnel_local_host": args.tunnel_local_host,
        "tunnel_extra_args": args.tunnel_extra_args,
    }
    if args.tunnel:
        overrides["tunnel_enabled"] = True
    return replace(config, **{key: value for key, value in overrides.items() if value is not None})


if __name__ == "__main__":
    raise SystemExit(main())
