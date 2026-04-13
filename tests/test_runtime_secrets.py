import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import BotConfig
from app.runtime_secrets import MissingSecretError, load_runtime_secrets


class RuntimeSecretsTest(unittest.TestCase):
    def test_load_runtime_secrets_reads_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            secrets_file = Path(temp_dir, "secrets.env")
            secrets_file.write_text(
                "\n".join(
                    [
                        "DB_URL=postgres://example",
                        "API_KEY=secret",
                    ]
                ),
                encoding="utf-8",
            )

            config = BotConfig(secret_env_keys=["DB_URL"])
            with patch.dict(os.environ, {"BOT_SECRETS_FILE": str(secrets_file)}, clear=True):
                loaded_keys = load_runtime_secrets(config)
                db_url = os.getenv("DB_URL")
                api_key = os.getenv("API_KEY")

        self.assertEqual(loaded_keys, ["DB_URL"])
        self.assertEqual(db_url, "postgres://example")
        self.assertEqual(api_key, "secret")

    def test_load_runtime_secrets_raises_for_missing_required_secret(self) -> None:
        config = BotConfig(required_secret_env=["DB_URL"])

        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(MissingSecretError) as context:
                load_runtime_secrets(config)

        self.assertEqual(context.exception.missing_keys, ["DB_URL"])


if __name__ == "__main__":
    unittest.main()
