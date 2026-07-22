from pathlib import Path


def test_render_staging_uses_free_web_service_without_persistent_disk() -> None:
    repository_root = Path(__file__).parents[1]
    render_config = (repository_root / "render.yaml").read_text(encoding="utf-8")

    assert "plan: free" in render_config
    assert "disk:" not in render_config
    assert "FITBIT_HEALTH_TOKEN_SEED_PATH" in render_config


def test_render_declares_web_oauth_bootstrap_configuration_without_values() -> None:
    repository_root = Path(__file__).parents[1]
    render_config = (repository_root / "render.yaml").read_text(encoding="utf-8")

    assert "OAUTH_BOOTSTRAP_PASSWORD" in render_config
    assert "OAUTH_COOKIE_SECRET" in render_config
    assert "GOOGLE_OAUTH_REDIRECT_URI" in render_config
    assert "bootstrap-password" not in render_config
    assert "cookie-signing-secret" not in render_config


def test_render_declares_mcp_oauth_metadata_configuration() -> None:
    repository_root = Path(__file__).parents[1]
    render_config = (repository_root / "render.yaml").read_text(encoding="utf-8")

    assert "MCP_OAUTH_ISSUER_URL" in render_config
    assert "MCP_OAUTH_RESOURCE_URL" in render_config


def test_render_declares_mcp_authorization_configuration_without_values() -> None:
    repository_root = Path(__file__).parents[1]
    render_config = (repository_root / "render.yaml").read_text(encoding="utf-8")

    assert "MCP_OAUTH_CLIENT_ID" in render_config
    assert "MCP_OAUTH_REDIRECT_URI" in render_config
    assert "MCP_OAUTH_OWNER_PASSWORD" in render_config
    assert "chatgpt-public-client" not in render_config
    assert "owner-password" not in render_config
