"""Cross-layer contracts using command runners; never access a real device."""
from pathlib import Path
from threading import Event, Thread

from Reco.model_interface import TOOLS
from src.tools.device.operations import create_event_hub_tools
from src.tools.device.pages import create_page_tools
from src.tools.device.screenshots import screenshot_content
from src.device.services.operations import DeviceOperationService
from src.device.services.page.bridge import PageBridge
from src.device.types.results import CommandResult, DeviceError
from src.tools.shared.safety import DANGEROUS_TOOL_POLICY_METADATA_KEY


PNG = b"\x89PNG\r\n\x1a\nfixture"


def test_screenshot_and_download_share_operation_lock():
    hub = DeviceOperationService()
    captured, release, started, stopped = Event(), Event(), Event(), Event()
    calls = []
    results = []

    def runner(args):
        calls.append(args)
        if "screencap" in args:
            captured.set()
            assert release.wait(2)
        elif "pull" in args:
            Path(args[-1]).write_bytes(PNG)
        elif "force-stop" in args:
            stopped.set()
        return CommandResult(args, 0, "", "")

    hub.executor.runner = runner
    def capture():
        results.append(hub.capture_png())
    def stop():
        started.set()
        results.append(hub.close_package("example.app"))
    first, second = Thread(target=capture), Thread(target=stop)
    first.start()
    try:
        assert captured.wait(2)
        second.start()
        assert started.wait(2)
        assert not stopped.wait(0.05)
    finally:
        release.set()
        first.join(2)
        if second.ident is not None:
            second.join(2)
    assert not first.is_alive() and not second.is_alive()
    assert PNG in results and "" in results
    assert [args[1] for args in calls] == ["shell", "pull", "shell"]


def test_screenshot_failure_keeps_agent_exception_boundary():
    hub = DeviceOperationService()
    hub.executor.runner = lambda args: CommandResult(args, 1, "", "capture denied")
    result = hub.capture_png()
    assert isinstance(result, DeviceError)
    import pytest
    with pytest.raises(ValueError, match="capture denied"):
        screenshot_content(hub)


def test_page_tool_schema_and_policy_identity_survive_move():
    bridge = PageBridge(lambda: "<hierarchy/>")
    tools = create_page_tools(bridge)
    assert [(tool.name, tool.description, tool.args_schema) for tool in tools] == [
        (spec["name"], spec["description"], spec["input_schema"]) for spec in TOOLS
    ]
    for tool in create_event_hub_tools(tool_names=("run_package", "close_package")):
        policy = tool.metadata[DANGEROUS_TOOL_POLICY_METADATA_KEY]
        assert policy.policy_id == f"src.device.operations.DeviceOperationService.{tool.name}"


def test_failed_capture_does_not_return_stale_page():
    hub = DeviceOperationService()
    hub.page_bridge.observe('<hierarchy><node text="old" bounds="[0,0][10,10]"/></hierarchy>')
    hub.executor.runner = lambda args: CommandResult(args, 1, "", "dump failed")
    content, artifact = hub.page_bridge.refresh()
    assert artifact is None
    assert content["ok"] is False
    import pytest
    with pytest.raises(ValueError, match="Read a page"):
        hub.page_bridge.snapshot()


def test_empty_package_never_executes_command():
    hub = DeviceOperationService()
    def runner(args):
        raise AssertionError("must not execute")
    hub.executor.runner = runner
    assert hub.run_package(" ").error_type == "validation_error"
    assert hub.close_package("").error_type == "validation_error"
