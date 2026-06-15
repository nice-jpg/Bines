from __future__ import annotations

import importlib
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

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
                        "DEEPSEEK_BASE_URL=https://example.test/v1",
                        "DEEPSEEK_API_KEYs=test-key",
                        "DEEPSEEK_MODEL=test-model",
                    ]
                ),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {}, clear=True):
                config = model.load_model_config(env_path)

        self.assertEqual(config.base_url, "https://example.test/v1")
        self.assertEqual(config.api_key, "test-key")
        self.assertEqual(config.model, "test-model")

    def test_build_model_passes_config_to_chat_openai(self) -> None:
        model = importlib.import_module("model")
        with patch.dict(
            os.environ,
            {
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
            },
        )


if __name__ == "__main__":
    unittest.main()
