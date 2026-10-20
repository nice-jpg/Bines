# Device module notes

See `docs/device-architecture.md` for the three-layer design and validation limits.

## Layers

- `commands/`: `AndroidCommandExecutor` owns the finite adb command interface.
  Every command returns `CommandResult` with normalized `DeviceError` on failure,
  including runner exceptions and explicit errors printed with exit code zero.
  No public arbitrary shell API, gesture policy, Reco or LangChain dependency.
  `shadow_control.py` is the existing nScreen SDK boundary.
- `services/`: `DeviceOperationService` owns one executor, `GestureReplay`, and
  `PageBridge`, using a shared RLock for device operations and page consistency.
  `page/` owns Reco queries, immutable snapshots, change hints and page algorithms.
  PageBridge reads commands directly, never calls back into the operation service.
  Offline callers may supply an XML callback without constructing a device.
- `src/tools/device/`: device, navigation and Reco tool factories plus PNG message
  encoding. Preserve schemas, role visibility, metadata and policy identities.
- `types/`: results, observations, gestures, page evidence and image/status data.

Old forwarding modules have been removed. Business callers import public device
capabilities and types from `src.device`. Within device, use canonical internal
imports to avoid package initialization cycles. Tool factories are exposed from
`src.tools.device`; device must never import that Agent-facing package.

## Gesture and native contracts

`GestureReplay` alone chooses common gesture recordings and applies independent
integer X/Y offsets in [-2, 2]. Uncommon gestures, currently swipe_back, use the
fixed recording and unmodified coordinates. The command executor never randomizes.

Deployment compares actual remote files against required recording filenames,
including swipe_back. Upload/conversion failures must not mark deployment ready.
Pure conversion preserves getevent parsing, coordinate origin normalization and
PIAR1 packet encoding. Do not modify recordings or native code as part of Python
refactoring; do not compile or run native replay without a separate task.

Default device paths remain `/sdcard/Documents/actions/`, `/dev/input/event3`,
`/sdcard/window_dump.xml`, and `/data/local/tmp/pi_input_replay`. UI capture uses
`/data/local/tmp/project -d` after disabling animations, not uiautomator dump.

## Page and screenshot contracts

Reco TOOLS is the single source for page tool names and schemas. First capture
returns the root view; subsequent observations return compact page_change_hint
with at most three non-ancestor entrances. Complete XML and snapshots stay in
artifacts. Preserve snapshot IDs, alias restoration and script view interfaces.

Screenshot capture and pull run under one operation lock; services return PNG
bytes or DeviceError. Agent adapters encode image_url and preserve the legacy
ValueError boundary. Do not bypass the service via direct executor access in
business code. Locks are per service, not globally per physical device.

## Tests

Use fake command runners; do not assume a connected Android device. Run the
project Python 3.14 interpreter, for example:

```bash
python -m pytest tests/test_device_actions.py tests/test_device_layers.py tests/test_event_hub.py tests/test_page_bridge.py
```

Add command-failure, deployment, randomization or cross-layer contract tests when
changing those behaviors. Real-device and nScreen integration require separate
validation; passing fake-runner tests does not establish device compatibility.
