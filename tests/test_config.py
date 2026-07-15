from app.config import Settings


def test_settings_load_from_env(monkeypatch):
    for k, v in {
        "RELEASE_BOT_TOKEN": "t", "CHANNEL_ID": "@c", "ADMIN_CHAT_ID": "42",
        "GITHUB_TOKEN": "g", "GITHUB_REPO": "o/r", "OPENROUTER_API_KEY": "k",
        "INITIAL_MARKER_SHA": "deadbeef",
    }.items():
        monkeypatch.setenv(k, v)
    s = Settings(_env_file=None)
    assert s.admin_chat_id == 42
    assert s.min_features_to_publish == 1
    assert s.schedule_cron == "0 12 * * FRI"
    assert s.prod_version_url.endswith("/api/v1/version")
