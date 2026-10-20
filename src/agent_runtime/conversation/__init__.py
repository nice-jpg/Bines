"""Single-conversation input independent of the Agent framework."""

from .message_block import MessageBlock
from .session import ConversationSession

__all__: list[str] = ["ConversationSession", "MessageBlock"]
