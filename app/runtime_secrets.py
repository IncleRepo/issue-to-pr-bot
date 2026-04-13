import os
from pathlib import Path

from app.config import BotConfig


DEFAULT_SECRETS_FILE = Path("/run/bot-secrets/secrets.env")
SECRETS_FILE_ENV = "BOT_SECRETS_FILE"


class MissingSecretError(RuntimeError):
    def __init__(self, missing_keys: list[str]) -> None:
        joined = ", ".join(missing_keys)
        super().__init__(f"필수 secret 환경 변수가 없습니다: {joined}")
        self.missing_keys = missing_keys


def load_runtime_secrets(config: BotConfig) -> list[str]:
    load_secrets_file()
    ensure_required_secret_env(config)
    available_keys = [key for key in config.secret_env_keys if os.getenv(key)]
    return sorted(set(available_keys))


def load_secrets_file() -> list[str]:
    path = get_secrets_file_path()
    if not path.exists() or not path.is_file():
        return []

    loaded_keys: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or key in os.environ:
            continue

        os.environ[key] = unquote_env_value(value)
        loaded_keys.append(key)

    return loaded_keys


def get_secrets_file_path() -> Path:
    configured = os.getenv(SECRETS_FILE_ENV)
    if configured:
        return Path(configured)
    return DEFAULT_SECRETS_FILE


def ensure_required_secret_env(config: BotConfig) -> None:
    missing = [key for key in config.required_secret_env if not os.getenv(key)]
    if missing:
        raise MissingSecretError(missing)


def unquote_env_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value
