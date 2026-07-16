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
    assert s.deploy_poll_seconds == 180
    assert s.prod_version_url.endswith("/api/v1/version")
    assert s.feature_prefix_list == ["VIP Board"]


def test_feature_prefixes_parsed_from_env(monkeypatch):
    for k, v in {
        "RELEASE_BOT_TOKEN": "t", "CHANNEL_ID": "@c", "ADMIN_CHAT_ID": "42",
        "GITHUB_TOKEN": "g", "GITHUB_REPO": "o/r", "OPENROUTER_API_KEY": "k",
        "INITIAL_MARKER_SHA": "deadbeef", "FEATURE_PREFIXES": "VIP Board, Live Ops",
    }.items():
        monkeypatch.setenv(k, v)
    s = Settings(_env_file=None)
    assert s.feature_prefix_list == ["VIP Board", "Live Ops"]
