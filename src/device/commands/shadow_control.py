"""Typed boundary for the nScreen human-authentication control service."""

from pydantic import TypeAdapter

from src.device.types.shadow import ShadowServiceStatus

_STATUS_ADAPTER: TypeAdapter[ShadowServiceStatus] = TypeAdapter(ShadowServiceStatus)


def start_shadow_service() -> ShadowServiceStatus:
    from nScreen import shadow_root

    return _STATUS_ADAPTER.validate_python(shadow_root.start_shadow_service(), strict=True)


def stop_shadow_service() -> ShadowServiceStatus:
    from nScreen import shadow_root

    return _STATUS_ADAPTER.validate_python(shadow_root.stop_shadow_service(), strict=True)
