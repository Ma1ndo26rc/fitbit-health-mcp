from pathlib import Path


def test_render_staging_uses_free_web_service_without_persistent_disk() -> None:
    repository_root = Path(__file__).parents[1]
    render_config = (repository_root / "render.yaml").read_text(encoding="utf-8")

    assert "plan: free" in render_config
    assert "disk:" not in render_config
    assert "FITBIT_HEALTH_TOKEN_SEED_PATH" in render_config

