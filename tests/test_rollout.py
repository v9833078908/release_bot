from app.rollout import build_rollout_context, parse_env_flags, summarize_flag_value


def test_parse_env_flags_ignores_comments_and_blank_lines():
    raw = """
    # Comment
    MCP_ENABLED=false
    CASES_READ_PROJECT_IDS=
    CORPUS_EXPLORE_ENABLED=*
    """
    assert parse_env_flags(raw) == {
        "MCP_ENABLED": "false",
        "CASES_READ_PROJECT_IDS": "",
        "CORPUS_EXPLORE_ENABLED": "*",
    }


def test_summarize_flag_value_redacts_uuids():
    assert summarize_flag_value("") == "OFF (empty)"
    assert summarize_flag_value("false") == "OFF (false)"
    assert summarize_flag_value("true") == "ON (true)"
    assert summarize_flag_value("*") == "ALL projects (*)"
    u1 = "f01134a9-99ff-4383-a6f0-64dcac5ccb1b"
    u2 = "5023ab64-42e3-486b-ab93-998e77cb4e1d"
    assert summarize_flag_value(u1) == "canary (1 project)"
    assert summarize_flag_value(f"{u1},{u2}") == "canary (2 projects)"
    assert u1 not in summarize_flag_value(u1)


def test_build_rollout_context_reports_changes_and_off_flags():
    base = "MCP_ENABLED=false\nCASES_READ_PROJECT_IDS=\nTOPICS_FACETS_GROWTH_PROJECT_IDS=*\nEMAIL_ALERT_PROJECT_IDS=*\nCORPUS_EXPLORE_ENABLED=\n"
    head = "MCP_ENABLED=false\nCASES_READ_PROJECT_IDS=\nCORPUS_EXPLORE_ENABLED=*\n"
    ctx = build_rollout_context(base, head)
    assert "CORPUS_EXPLORE_ENABLED: OFF (empty) -> ALL projects (*)" in ctx
    # Flags that were already ALL (*) before retirement are not reported as newly enabled
    assert "TOPICS_FACETS_GROWTH_PROJECT_IDS" not in ctx
    assert "EMAIL_ALERT_PROJECT_IDS" not in ctx
    assert "MCP_ENABLED: OFF (false)" in ctx
    assert "CASES_READ_PROJECT_IDS: OFF (empty)" in ctx


def test_build_rollout_context_reports_retired_canary_flag():
    base = "TOPICS_FACETS_GROWTH_PROJECT_IDS=f01134a9-99ff-4383-a6f0-64dcac5ccb1b\n"
    head = "MCP_ENABLED=false\n"
    ctx = build_rollout_context(base, head)
    assert "TOPICS_FACETS_GROWTH_PROJECT_IDS: retired from flags" in ctx
