import os
import shutil
from collections.abc import Callable, Mapping
from pathlib import Path

from fitbit_health.auth import ensure_private_file
from fitbit_health.mcp_tools import HealthMCPService


TOKEN_PATH_ENV = "FITBIT_HEALTH_TOKEN_PATH"
CLIENT_SECRET_SOURCE_ENV = "FITBIT_HEALTH_CLIENT_SECRET_PATH"
DEFAULT_TOKEN_PATH = Path(".private") / "token.json"


class CredentialStorageError(RuntimeError):
    """Raised when configured credential storage cannot be prepared safely."""


def resolve_token_path(
    environ: Mapping[str, str] | None = None,
    cwd: Path | None = None,
) -> Path:
    """Resolve the local or persistent-disk token path without reading it."""
    environment = os.environ if environ is None else environ
    base = Path.cwd() if cwd is None else cwd
    configured = Path(environment.get(TOKEN_PATH_ENV, str(DEFAULT_TOKEN_PATH)))
    if not configured.is_absolute():
        configured = base / configured
    return configured.expanduser().resolve()


def create_health_service_factory(
    token_path: Path,
    environ: Mapping[str, str] | None = None,
) -> Callable[[], HealthMCPService]:
    """Create the existing service at the root implied by its token path."""
    resolved_token_path = token_path.expanduser().resolve()
    if (
        resolved_token_path.name != "token.json"
        or resolved_token_path.parent.name != ".private"
    ):
        raise CredentialStorageError(
            "Token path must end with .private/token.json."
        )

    environment = os.environ if environ is None else environ
    client_secret_source = environment.get(CLIENT_SECRET_SOURCE_ENV)
    service_root = resolved_token_path.parent.parent

    def create_service() -> HealthMCPService:
        if client_secret_source:
            _stage_client_secret(Path(client_secret_source), service_root)
        return HealthMCPService(service_root)

    return create_service


def _stage_client_secret(source: Path, service_root: Path) -> None:
    resolved_source = source.expanduser().resolve()
    if not (
        resolved_source.name.startswith("client_secret_")
        and resolved_source.suffix == ".json"
        and resolved_source.is_file()
    ):
        raise CredentialStorageError(
            "Configured Google client credential is unavailable."
        )

    service_root.mkdir(parents=True, exist_ok=True)
    destination = service_root / resolved_source.name
    if resolved_source != destination.resolve():
        shutil.copyfile(resolved_source, destination)
    ensure_private_file(destination)
