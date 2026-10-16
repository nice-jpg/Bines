"""Public device capabilities and data contracts; Agent wrappers live in src.tools.device."""

from src.device.commands.executor import (
    AndroidCommandExecutor,
    Runner,
)
from src.device.services.gestures import (
    GestureReplay,
)
from src.device.services.operations import (
    DeviceOperationService,
)
from src.device.services.page.bridge import (
    PAGE_TOOL_NAMES,
    PageBridge,
    PageOperationResult,
)
from src.device.services.page.change_hint import (
    change_hint,
    root_change_ratio,
)
from src.device.services.page.optimize_xml import (
    Rectangle,
    optimize,
)
from src.device.services.page.similarity import (
    PageSimilarityError,
    PageSimilarityEvaluator,
    PageSimilarityResult,
)
from src.device.services.page.view import (
    MissingPageAlias,
    ReadOnlyPageView,
    freeze_page,
    region_paths,
    restore_page,
)
from src.device.services.translator import (
    build_replay_packet,
    group_events_by_syn_report,
    normalize_events_to_origin,
    parse_action_log,
    parse_getevent_line,
)
from src.device.types.gestures import (
    GestureSpec,
    ParsedEvent,
    ReplayFrame,
    ReplayTarget,
)
from src.device.types.images import (
    ImageContent,
    ImageUrl,
)
from src.device.types.observations import (
    CollectionSwipe,
    SwipeObservation,
)
from src.device.types.page import (
    ChangedRegion,
    PageArtifact,
    PageChangeHint,
    PageSnapshot,
    artifact_snapshot,
)
from src.device.types.results import (
    CommandResult,
    DeviceError,
    make_error_result,
)
from src.device.types.shadow import (
    ShadowAgentStatus,
    ShadowParameters,
    ShadowServiceStatus,
)

__all__: list[str] = [
    "AndroidCommandExecutor",
    "Runner",
    "DeviceOperationService",
    "GestureReplay",
    "PageBridge",
    "PageOperationResult",
    "PAGE_TOOL_NAMES",
    "ReadOnlyPageView",
    "MissingPageAlias",
    "freeze_page",
    "restore_page",
    "region_paths",
    "change_hint",
    "root_change_ratio",
    "PageSimilarityEvaluator",
    "PageSimilarityResult",
    "PageSimilarityError",
    "Rectangle",
    "optimize",
    "parse_action_log",
    "parse_getevent_line",
    "normalize_events_to_origin",
    "group_events_by_syn_report",
    "build_replay_packet",
    "CommandResult",
    "DeviceError",
    "make_error_result",
    "CollectionSwipe",
    "SwipeObservation",
    "PageSnapshot",
    "PageArtifact",
    "PageChangeHint",
    "ChangedRegion",
    "artifact_snapshot",
    "ImageContent",
    "ImageUrl",
    "GestureSpec",
    "ReplayTarget",
    "ParsedEvent",
    "ReplayFrame",
    "ShadowServiceStatus",
    "ShadowAgentStatus",
    "ShadowParameters",
]
