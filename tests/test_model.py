from __future__ import annotations

import importlib
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class ModelConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_langchain_openai = sys.modules.get("langchain_openai")
        fake_module = types.ModuleType("langchain_openai")

        class ChatOpenAI:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        fake_module.ChatOpenAI = ChatOpenAI
        sys.modules["langchain_openai"] = fake_module
        sys.modules.pop("model", None)

    def tearDown(self) -> None:
        if self.original_langchain_openai is None:
            sys.modules.pop("langchain_openai", None)
        else:
            sys.modules["langchain_openai"] = self.original_langchain_openai
        sys.modules.pop("model", None)

    def test_load_model_config_reads_workspace_env_file(self) -> None:
        model = importlib.import_module("model")
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text(
                "\n".join(
                    [
                        "MODEL_PROVIDER=deepseek",
                        "DEEPSEEK_BASE_URL=https://example.test/v1",
                        "DEEPSEEK_API_KEYs=test-key",
                        "DEEPSEEK_MODEL=test-model",
                    ]
                ),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {}, clear=True):
                config = model.load_model_config(env_path)

        self.assertEqual(config.provider, "deepseek")
        self.assertEqual(config.base_url, "https://example.test/v1")
        self.assertEqual(config.api_key, "test-key")
        self.assertEqual(config.model, "test-model")

    def test_build_model_passes_config_to_chat_openai(self) -> None:
        model = importlib.import_module("model")
        with patch.dict(
            os.environ,
            {
                "MODEL_PROVIDER": "deepseek",
                "DEEPSEEK_BASE_URL": "https://example.test/v1",
                "DEEPSEEK_API_KEY": "test-key",
                "DEEPSEEK_MODEL": "test-model",
            },
            clear=True,
        ):
            chat_model = model.build_model()

        self.assertEqual(
            chat_model.kwargs,
            {
                "base_url": "https://example.test/v1",
                "api_key": "test-key",
                "model": "test-model",
                "streaming": False,
                "use_responses_api": False,
            },
        )

    def test_build_model_uses_codex_backend_selected_by_config(self) -> None:
        model = importlib.import_module("model")
        create_chat_model = Mock(return_value=object())
        with patch.dict(
            os.environ,
            {
                "MODEL_PROVIDER": "codex",
                "CODEX_MODEL": "gpt-configured",
                "CODEX_REASONING_EFFORT": "high",
                "CODEX_PROMPT_CACHE_KEY": "cache-key",
                "CODEX_STREAM_MAX_RETRIES": "7",
            },
            clear=True,
        ), patch.object(model, "create_chat_model", create_chat_model):
            chat_model = model.build_model()

        self.assertIs(chat_model, create_chat_model.return_value)
        create_chat_model.assert_called_once_with(
            model="gpt-configured",
            reasoning_effort="high",
            prompt_cache_key="cache-key",
            stream_max_retries=7,
        )

    def test_load_model_config_rejects_invalid_codex_retry_count(self) -> None:
        model = importlib.import_module("model")
        with patch.dict(
            os.environ,
            {
                "MODEL_PROVIDER": "codex",
                "CODEX_MODEL": "gpt-configured",
                "CODEX_STREAM_MAX_RETRIES": "-1",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "non-negative integer"):
                model.load_model_config()

    def test_load_model_config_rejects_unknown_provider(self) -> None:
        model = importlib.import_module("model")
        with patch.dict(os.environ, {"MODEL_PROVIDER": "unknown"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "Unsupported MODEL_PROVIDER"):
                model.load_model_config()


if __name__ == "__main__":
    unittest.main()
