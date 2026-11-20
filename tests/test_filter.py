from app.filter import (
    Commit,
    collapse_commits,
    detect_surfaces,
    filter_commits,
    is_release_worthy,
    parse_commit,
    prioritize_for_enrichment,
)


def test_parse_feat_with_scope_and_body():
    c = parse_commit("sha1", "feat(topics): add overview card\n\nDetailed note\n\nCo-Authored-By: Bot <b@x>")
    assert c == Commit("sha1", "feat", "topics", "add overview card", False, body="Detailed note")


def test_parse_breaking():
    assert parse_commit("s", "fix(api)!: drop legacy field").breaking is True


def test_non_conventional_product_commits_are_kept():
    c_add = parse_commit("s1", "Add scoped store review CSV and XLSX exports")
    c_fix = parse_commit("s2", "Fix AutoAnswer store status and default backlog to seven days")
    c_pref = parse_commit("s3", "Support Playbook: drop sidebar item, enter from Replies")
    assert c_add.type == "feat" and is_release_worthy(c_add)
    assert c_fix.type == "fix" and is_release_worthy(c_fix)
    assert c_pref.type == "feat" and c_pref.scope == "Support Playbook" and is_release_worthy(c_pref)


def test_release_worthy_keeps_feat_fix_perf_drops_internal_types():
    assert is_release_worthy(Commit("s", "feat", "topics", "x"))
    assert is_release_worthy(Commit("s", "fix", "api", "x"))
    assert is_release_worthy(Commit("s", "perf", "api", "x"))
    assert not is_release_worthy(Commit("s", "docs", "plan", "x"))
    assert not is_release_worthy(Commit("s", "chore", None, "x"))
    assert not is_release_worthy(Commit("s", "refactor", "autoanswer", "x"))
    assert not is_release_worthy(Commit("s", "test", None, "x"))


def test_release_worthy_drops_noise_scopes_and_subjects():
    assert not is_release_worthy(Commit("s", "feat", "research", "x"))
    assert not is_release_worthy(Commit("s", "feat", "eval-labelling", "offline xlsx export"))
    assert not is_release_worthy(Commit("s", "feat", "website", "new landing block"))
    assert not is_release_worthy(parse_commit("s", "Cases prototype: layout behind flag"))
    assert not is_release_worthy(parse_commit("s", "Record production release verification"))
    assert not is_release_worthy(parse_commit("s", "Merge branch 'main' into feat/x"))
    assert not is_release_worthy(parse_commit("s", "Add prod DB retention prune script"))


def test_file_path_filter_drops_non_product_commits_and_keeps_product():
    raw = [
        ("s1", "Add scoped store review CSV and XLSX exports"),
        ("s2", "feat(eval): export workbook"),
        ("s3", "Add docs note"),
    ]
    files = {
        "s1": ("frontend/src/pages/StoreRatingsPage.jsx", "backend/app/analytics/export_service.py"),
        "s2": ("backend/scripts/eval_export.py", "backend/tests/test_eval.py"),
        "s3": ("docs/plans/foo.md", "CODEBASE_CONTEXT.md"),
    }
    kept = filter_commits(raw, files_by_sha=files)
    assert [c.sha for c in kept] == ["s1"]
    assert "/store-ratings (Рейтинги сторов)" in kept[0].surfaces


def test_detect_surfaces():
    s = detect_surfaces((
        "frontend/src/pages/StoreRatingsPage.jsx",
        "backend/app/analytics/export_service.py",
        "backend/app/connectors/zendesk_text.py",
    ))
    assert "/store-ratings (Рейтинги сторов)" in s
    assert "/store-ratings (Экспорт отзывов)" in s
    assert "Zendesk" in s


def test_feature_prefix_promoted_to_feat():
    c = parse_commit("s", "VIP Board: connection-loading gate", feature_prefixes=("VIP Board",))
    assert c.type == "feat" and c.scope == "VIP Board" and c.subject == "connection-loading gate"


def test_conventional_release_type_takes_precedence_over_prefix():
    c = parse_commit("s", "feat(topics): a", feature_prefixes=("feat",))
    assert c == Commit("s", "feat", "topics", "a", False)


def test_collapse_commits_shrinks_high_volume_scope():
    corpus = [
        Commit(f"c{i}", "feat", "corpus", f"DEF-{i} T{i} internal step")
        for i in range(20)
    ]
    ui_commit = Commit(
        "c_ui", "feat", "corpus", "readable answers",
        files=("frontend/src/components/insight-chat/AIBar.jsx",),
    )
    other = Commit("x1", "feat", None, "Add CSV export", files=("frontend/src/pages/StoreRatingsPage.jsx",))
    collapsed = collapse_commits(corpus + [ui_commit, other], scope_threshold=10, max_total=30)
    shas = [c.sha for c in collapsed]
    assert "c_ui" in shas and "x1" in shas
    assert len(collapsed) < len(corpus) + 2


def test_prioritize_for_enrichment_prefers_non_internal_newest():
    internal = [Commit(f"i{i}", "feat", "corpus", f"DEF-{i} internal") for i in range(10)]
    real = Commit("r1", "feat", None, "Add CSV export")
    picked = prioritize_for_enrichment(internal + [real], limit=3)
    assert picked[0].sha == "r1"


def test_collapse_commits_never_evicts_newest_ui_commits_for_older_unenriched():
    older = [Commit(f"o{i}", "feat", None, f"Early branch feature {i}") for i in range(40)]
    newest_ui = Commit(
        "ui99", "feat", None, "Add scoped store review CSV and XLSX exports",
        files=("frontend/src/pages/StoreRatingsPage.jsx",),
    )
    collapsed = collapse_commits(older + [newest_ui], scope_threshold=10, max_total=15)
    shas = [c.sha for c in collapsed]
    assert "ui99" in shas
    assert "o39" in shas  # newest unenriched kept over oldest o0
    assert "o0" not in shas
