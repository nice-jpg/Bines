# src/device Agent Notes

This directory provides synchronous Android-device operations and recorded
touch-action translation.

## Modules

- `adapter.py`
  - `AndroidDevice` is the adb-backed device facade.
  - `CommandResult` wraps raw subprocess output and raises through
    `check_returncode()`.
  - Default paths:
    - actions directory: `/sdcard/Documents/actions/`
    - input device: `/dev/input/event3`
    - UI dump path: `/sdcard/window_dump.xml`

- `translator.py`
  - Parses `getevent` logs from local action files.
  - Normalizes the first recorded touch coordinate to `(0, 0)` before packet
    generation, so replay-time `x/y` deltas place the gesture.
  - Converts parsed events into PIAR replay packets for the native helper.
  - Pushes missing translated actions to the device action directory.

- `native/`
  - Contains the `pi_input_replay` Android ARM64 helper and source.
  - The helper consumes `PIAR1` packets and applies optional `x/y` coordinate
    deltas at replay time.

- `actions/`
  - Local source action recordings.
  - Files are treated as `getevent` text logs by `translator.translate()`.

## AndroidDevice API

Use `AndroidDevice(serial="", adb_path="adb", runner=None)` for device access.
The optional `runner` makes tests independent of real adb.

Core operations:

- `shell(command, root=False) -> str`
- `shell_raw(command, root=False) -> CommandResult`
- `dump_ui() -> str`
- `execute_file(device_file_path, args=None, root=False) -> str`
- `list_files(device_dir) -> list[str]`
- `push_file(local_path, device_dir) -> str`
- `get_supported_actions(device_dir=DEFAULT_ACTION_DIR) -> list[str]`
- `add_action(local_path, device_dir=DEFAULT_ACTION_DIR) -> str`
- `act(action_name, xy, device_dir=DEFAULT_ACTION_DIR, input_device=DEFAULT_INPUT_DEVICE) -> str`

Current `act()` behavior:

- Ensures the action name exists in `get_supported_actions()`.
- Resolves the remote action path as `{device_dir}/{action_name}`.
- Executes `/data/local/tmp/pi_input_replay` as root with:
  `input_device`, `action_path`, `x`, `y`.

## Action Translation

Use `check_actions(device, local_dir="src/device/actions", device_dir=DEFAULT_ACTION_DIR)`
to sync missing local actions to the device.

Flow:

1. Read local action filenames from `local_dir`.
2. Compare them with `device.get_supported_actions(device_dir)`.
3. For each missing action, call `translate(action_path, device, device_dir)`.
4. `translate()` parses the getevent log, shifts all X/Y touch coordinates by
   the first recorded X/Y value, builds a PIAR packet, writes a temporary
   same-name file, and calls `device.add_action()`.

PIAR packet format:

- Header: `b"PIAR1\0\0\0"`
- Frames are split on `EV_SYN` / `SYN_REPORT`.
- Each frame writes:
  - `delay_us` as little-endian `uint32`
  - event count as little-endian `uint32`
- Each event writes:
  - type as little-endian `uint16`
  - code as little-endian `uint16`
  - value as little-endian `int32`

The parser supports both pathless and device-path `getevent` lines, symbolic
tokens such as `EV_ABS`, `ABS_MT_POSITION_X`, `BTN_TOUCH`, `DOWN`/`UP`, and
signed hex values like `ffffffff -> -1`.

Only touch coordinate events are shifted during origin normalization:
`ABS_MT_POSITION_X`, `ABS_X`, `ABS_MT_POSITION_Y`, and `ABS_Y`. Pressure,
tracking id, slot, key, and sync events remain unchanged.

## Tests

Run:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/bines_pycache python3 -m py_compile src/device/*.py tests/test_device_actions.py
PYTHONPYCACHEPREFIX=/private/tmp/bines_pycache python3 -m unittest tests.test_device_actions
```

The tests use fake adb runners and do not require a connected Android device.

## Important Cautions

- Do not import `src.device` through the top-level `src` package in tests unless
  LangChain dependencies are installed; tests currently use `PYTHONPATH=src`
  and import `device.*` directly.
- Keep replay output as PIAR binary unless the native helper contract changes.
