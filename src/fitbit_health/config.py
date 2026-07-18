import json
from pathlib import Path


SCOPES = (
    "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly",
    "https://www.googleapis.com/auth/googlehealth.sleep.readonly",
    "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly",
)


class ConfigError(RuntimeError):
    """Raised when the local application configuration is unusable."""


def find_installed_credentials(root: Path) -> Path:
    """Return the sole usable installed-app OAuth credential under *root*."""
    installed: list[Path] = []
    for path in sorted(root.glob("client_secret_*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and isinstance(payload.get("installed"), dict):
            installed.append(path)

    if not installed:
        raise ConfigError("未找到 Google OAuth 桌面设备凭据 JSON。")
    if len(installed) > 1:
        raise ConfigError("找到多个桌面设备凭据，请只保留一个 client_secret_*.json。")
    return installed[0]
