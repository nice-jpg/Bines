from typing import Final


class DeviceActions:
    TAP: str = "tap"
    SWIPE_UP: str = "swipe_up"
    SWIPE_DOWN: str = "swipe_down"
    SWIPE_UP_LONG: str = "swipe_up_long"
    SWIPE_TO_RANDOM: str = "swipe_to_random"
    SWIPE_BACK: str = "swipe_back"


HOT_ACTIONS: Final[list[str]] = [
    DeviceActions.TAP,
    DeviceActions.SWIPE_UP,
    DeviceActions.SWIPE_DOWN,
    DeviceActions.SWIPE_UP_LONG,
    DeviceActions.SWIPE_TO_RANDOM,
]

ALL_ACTIONS: Final[list[str]] = [*HOT_ACTIONS, DeviceActions.SWIPE_BACK]
