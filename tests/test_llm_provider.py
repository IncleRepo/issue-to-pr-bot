import tempfile
import unittest
from pathlib import Path

from app.bot import BotRuntimeOptions
from app.llm_provider import ProviderExecutionRequest, ensure_supported_provider, get_supported_providers


class LlmProviderTest(unittest.TestCase):
    def test_get_supported_providers_lists_codex(self) -> None:
        self.assertEqual(get_supported_providers(), ["codex"])

    def test_ensure_supported_provider_rejects_unknown_provider(self) -> None:
        with self.assertRaises(ValueError):
            ensure_supported_provider("claude")

    def test_provider_execution_request_stores_runtime_options(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            request = ProviderExecutionRequest(
                workspace=Path(temp_dir),
                prompt="hello",
                runtime_options=BotRuntimeOptions(mode="codex", provider="codex", verify=True),
            )

        self.assertEqual(request.prompt, "hello")
        self.assertEqual(request.runtime_options.provider, "codex")


if __name__ == "__main__":
    unittest.main()
