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
