"""Human authentication handoff tools."""

import json
from collections.abc import Callable, Mapping
from typing import Final, TypedDict

from langchain_core.tools import StructuredTool

from src.common.event_log import WorkspaceEventLogger
from src.device import DeviceOperationService, ShadowServiceStatus
from src.serialization.json import json_object
from src.tools.shared.factory import create_tool
from src.tools.shared.xml import escape_attr as _escape_attr
from src.tools.shared.xml import escape_text as _escape_text

SHADOW_AUTH_URL: Final[str] = "http://139.224.44.6:9080"


class CaptchaAuthenticationTool:
    """Captcha authentication tools, including nScreen remote control lifecycle."""

    def __init__(
        self,
        notifier: Callable[[str], None] | None = None,
        device_operations: DeviceOperationService | None = None,
    ) -> None:
        self.device_operations: DeviceOperationService = (
            device_operations if device_operations is not None else DeviceOperationService()
        )
        self.logger: WorkspaceEventLogger = WorkspaceEventLogger()
        self.notifier: Callable[[str], None] | None = notifier

    def authenticate_captcha(
        self,
        current_path: str,
        reason: str = "",
        evidence: str = "",
    ) -> str:
        """Start captcha remote control access and notify the user how to authenticate."""

        normalized_path: str = str(current_path or "").strip()
        normalized_reason: str = str(reason or "").strip()
        normalized_evidence: str = str(evidence or "").strip()
        self.logger.log(
            "captcha_authentication",
            "Captcha authentication was handed off to a human operator.",
            details={
                "current_path": normalized_path,
                "reason": normalized_reason,
                "evidence": normalized_evidence,
            },
        )
        return self.start_authentication_session()

    def captcha_authenticated(
        self,
        current_path: str = "",
        evidence: str = "",
    ) -> str:
        """Stop captcha remote control access after the user reports completion."""

        normalized_path: str = str(current_path or "").strip()
        normalized_evidence: str = str(evidence or "").strip()
        self.logger.log(
            "captcha_authenticated",
            "Human operator reported captcha authentication completed.",
            details={
                "current_path": normalized_path,
                "evidence": normalized_evidence,
            },
        )
        return self.stop_authentication_session()

    def start_authentication_session(self) -> str:
        """Start nScreen remote control access and notify the user how to authenticate."""

        try:
            result: ShadowServiceStatus = self.device_operations.start_shadow_service()
        except Exception as exc:  # noqa: BLE001 - tools return structured text instead of raising.
            return self._error_result("start_shadow_service", exc)

        self.logger.log(
            "shadow_service_start", "Started captcha remote control service.", {"result": json_object(result)}
        )
        self._notify_user_for_authentication()
        return _xml_block(
            "shadow_service",
            {
                "operation": "start",
                "auth_url": SHADOW_AUTH_URL,
            },
            result,
        )

    def stop_authentication_session(self) -> str:
        """Stop nScreen remote control access after captcha authentication."""

        try:
            result: ShadowServiceStatus = self.device_operations.stop_shadow_service()
        except Exception as exc:  # noqa: BLE001 - tools return structured text instead of raising.
            return self._error_result("stop_shadow_service", exc)

        self.logger.log(
            "shadow_service_stop", "Stopped captcha remote control service.", {"result": json_object(result)}
        )
        return _xml_block("shadow_service", {"operation": "stop"}, result)

    def _notify_user_for_authentication(self) -> None:
        if self.notifier is None:
            return
        message: str = (
            "验证码认证需要人工协助。\n"
            f"请打开 {SHADOW_AUTH_URL} 操作移动设备完成认证。\n"
            "完成后请回复 `done`，我会关闭远程控制端口并继续流程。"
        )
        try:
            self.notifier(message)
        except Exception as exc:  # noqa: BLE001 - notification failure should not interrupt the tool.
            self.logger.log(
                "shadow_service_notice_error",
                "Failed to notify user about captcha remote control.",
                {"error": str(exc), "auth_url": SHADOW_AUTH_URL},
            )

    def _error_result(self, operation: str, exc: Exception) -> str:
        self.logger.log(
            "shadow_service_error",
            f"{operation} failed.",
            {"error": f"{type(exc).__name__}: {exc}", "operation": operation},
        )
        return _xml_block(
            "shadow_service_error",
            {"operation": operation},
            {"error": f"{type(exc).__name__}: {exc}"},
        )


class ShadowServiceError(TypedDict):
    error: str


def _xml_block(name: str, attrs: Mapping[str, str], payload: ShadowServiceStatus | ShadowServiceError) -> str:
    attr_text: str = "".join(f' {key}="{_escape_attr(value)}"' for key, value in attrs.items())
    return (
        f"<{name}{attr_text}>{_escape_text(json.dumps(payload, ensure_ascii=False, sort_keys=True))}</{name}>"
    )


def create_captcha_tools(service: CaptchaAuthenticationTool) -> list[StructuredTool]:
    return [
        create_tool(
            service.authenticate_captcha,
            description="Start human captcha authentication when the current UI shows verification. Stop the turn and wait for the user to report completion.",
        ),
        create_tool(
            service.captcha_authenticated,
            description="Use only after the user reports the captcha has been completed. Close remote control, then inspect the page before continuing.",
        ),
    ]
