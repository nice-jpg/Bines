# shadow_root env

`ShadowConfig.from_env()` reads every `*.env` file in this directory in sorted
filename order, then overlays `os.environ`.

Use `shadow_root.env` for the current runtime profile. It intentionally contains
only the active direct-to-remote WebRTC variables. Put temporary local overrides
in `local.env`; it is ignored by git.

Lines use simple dotenv syntax:

```sh
SHADOW_PORT=8080
SHADOW_WEBRTC_TRANSPORT=tcp_direct
```

Blank lines and lines beginning with `#` are ignored.
