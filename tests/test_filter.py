from app.filter import Commit, parse_commit, is_release_worthy, filter_commits


def test_parse_feat_with_scope():
    c = parse_commit("sha1", "feat(topics): add overview card\n\nbody")
    assert c == Commit("sha1", "feat", "topics", "add overview card", False)


def test_parse_breaking():
    assert parse_commit("s", "fix(api)!: drop legacy field").breaking is True


def test_non_conventional_returns_none():
    assert parse_commit("s", "random message") is None


def test_release_worthy_keeps_feat_fix_perf_drops_others():
    assert is_release_worthy(Commit("s", "feat", "topics", "x", False))
    assert is_release_worthy(Commit("s", "fix", "api", "x", False))
    assert is_release_worthy(Commit("s", "perf", "api", "x", False))
    assert not is_release_worthy(Commit("s", "docs", "plan", "x", False))
    assert not is_release_worthy(Commit("s", "chore", None, "x", False))


def test_release_worthy_drops_noise_scopes():
    assert not is_release_worthy(Commit("s", "feat", "research", "x", False))


def test_filter_commits_end_to_end():
    raw = [
        ("s1", "feat(topics): a"), ("s2", "docs(plan): b"),
        ("s3", "fix(alerts): c"), ("s4", "not conventional"),
        ("s5", "feat(research): internal"),
    ]
    assert [c.sha for c in filter_commits(raw)] == ["s1", "s3"]


def test_feature_prefix_promoted_to_feat():
    c = parse_commit("s", "VIP Board: connection-loading gate", feature_prefixes=("VIP Board",))
    assert c == Commit("s", "feat", "VIP Board", "connection-loading gate", False)


def test_one_word_feature_prefix_promoted():
    # single-word prefix collides with _CC's type slot; must still promote (review fix)
    c = parse_commit("s", "UI: dark mode toggle", feature_prefixes=("UI",))
    assert c == Commit("s", "feat", "UI", "dark mode toggle", False)


def test_feature_prefix_case_insensitive():
    assert parse_commit("s", "vip board: x", feature_prefixes=("VIP Board",)).type == "feat"


def test_feature_prefix_requires_colon():
    assert parse_commit("s", "VIP Board without colon", feature_prefixes=("VIP Board",)) is None


def test_feature_prefix_empty_subject_dropped():
    assert parse_commit("s", "VIP Board:   ", feature_prefixes=("VIP Board",)) is None


def test_non_prefixed_non_conventional_still_dropped():
    assert parse_commit("s", "Add prod DB retention prune script", feature_prefixes=("VIP Board",)) is None


def test_unlisted_one_word_conventional_still_dropped():
    assert not is_release_worthy(parse_commit("s", "UI: x"))   # UI not in allowlist -> non-release type


def test_conventional_release_type_takes_precedence_over_prefix():
    c = parse_commit("s", "feat(topics): a", feature_prefixes=("feat",))
    assert c == Commit("s", "feat", "topics", "a", False)      # release-type regex path wins


def test_filter_commits_promotes_prefix_and_keeps_dropping_noise():
    raw = [("s1", "VIP Board: gate"), ("s2", "chore: x"), ("s3", "Add script")]
    assert [c.sha for c in filter_commits(raw, feature_prefixes=("VIP Board",))] == ["s1"]
