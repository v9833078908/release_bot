import re
from collections import defaultdict
from dataclasses import dataclass

_CC = re.compile(
    r"^(?P<type>[A-Za-z][A-Za-z0-9_-]*)(?:\((?P<scope>[^)]+)\))?(?P<breaking>!)?:\s*(?P<subject>.+)$"
)

_TITLE_PREFIX = re.compile(r"^(?P<prefix>[A-Za-z][A-Za-z0-9 _-]{1,30}):\s+(?P<subject>.+)$")

RELEASE_TYPES = {"feat", "fix", "perf"}
NON_RELEASE_TYPES = {
    "docs", "doc", "test", "tests", "chore", "style", "refactor",
    "ci", "build", "ops", "eval", "evals", "wip", "prototype", "copy",
}

NOISE_SCOPES = {
    "research", "litellm", "plan", "plans", "docs", "doc",
    "eval", "evals", "eval-labelling", "telemetry", "website",
    "deploy", "infra", "alembic", "scripts", "tests", "test",
    "agents", "codebase_context", "cases-prototype", "cases prototype",
    "rollout", "flags", "seed", "llm", "prototype",
    "r1", "r1-gate", "r1-gc-gates", "r1-g6", "r1-tg", "r1-def1", "r1-iii5",
    "aa-ui", "corpus-capacity", "analyst", "game-metrics", "taxonomy", "monetization",
}

_NOISE_SUBJECT_RE = re.compile(
    r"^(?:"
    r"Merge (?:branch|remote-tracking branch|commit|tag|alembic|origin/|GitHub|main|updated main)|"
    r"Revert \"Cases prototype|"
    r"Cases prototype:|"
    r"(?:Record|Document|Capture|Measure|Audit) (?!.*export)|"
    r"Prepare synthetic |"
    r"Allow targeted release checks|"
    r"Retire globally enabled rollout flags|"
    r"Prevent concurrent pytest|"
    r"Fix (?:baseline-failing|cached settings leaking|flaky)|"
    r"(?:Update|Align|Fix) .* tests?(?:$| )|.* \(test-only\)$|"
    r"Add conservative scheduled Docker|"
    r"Add .* prune script$|"
    r"Wait for scroll restoration in .* test"
    r")",
    re.IGNORECASE,
)

_INTERNAL_SUBTASK_RE = re.compile(
    r"(?:"
    r"\bDEF-\d+|\bPlan \d+(?:\.\d+)?|\bS[0-3]-[ABCD]\d*|\bL0\d\b|\bW\d+ gate|\bG[56] |\bGC |\bT\d+\b"
    r"|demo seed|demo case|offline linker|eval runner|frozen runner|shadow spend|shadow check"
    r")",
    re.IGNORECASE,
)

_NON_PRODUCT_PREFIXES = (
    "docs/", "landing/", "scripts/", "backend/tests/", "backend/scripts/",
    "backend/alembic/", ".github/", ".planning/",
)
_NON_PRODUCT_EXACT = {
    "CODEBASE_CONTEXT.md", "CLAUDE.md", "AGENTS.md", "DEPLOYMENT.md",
    "DESIGN.md", "Makefile", ".gitignore", ".env.local.example", ".env.prod.example",
}

_SURFACE_RULES: tuple[tuple[str, str], ...] = (
    ("StoreRatingsPage", "/store-ratings (Рейтинги сторов)"),
    ("export_service", "/store-ratings (Экспорт отзывов)"),
    ("export_writers", "/store-ratings (Экспорт отзывов)"),
    ("TopicClustersPage", "/topics (Topics / Facets)"),
    ("topics-facets", "/topics (Topics / Facets)"),
    ("topics_facet_", "/topics (Topics / Facets)"),
    ("AlertsPage", "/alerts (Алерты)"),
    ("components/alerts/", "/alerts (Алерты)"),
    ("alert_detector", "/alerts (Алерты)"),
    ("ops_copilot/", "/alerts (Алерты)"),
    ("RepliesPage", "/replies (Ответы / AutoAnswer)"),
    ("components/autoanswer/", "/replies (Ответы / AutoAnswer)"),
    ("autoanswer/", "/replies (Ответы / AutoAnswer)"),
    ("support_drafts/", "/replies (Черновики ответов)"),
    ("SupportPlaybookPage", "/support-playbook (Support Playbook)"),
    ("FeedPage", "/feed (Лента)"),
    ("VipBoardPage", "/vip-board (VIP-доска)"),
    ("ChannelsPage", "/channels (Каналы)"),
    ("connectors/", "/channels (Источники данных)"),
    ("SettingsPage", "/settings (Настройки)"),
    ("organizations/", "/settings (Команда и настройки)"),
    ("DashboardPage", "/ (Дашборд)"),
    ("PlatformsVersionsPage", "/release-issues (Релизы и версии)"),
    ("SentimentPage", "/sentiment (Сентимент)"),
    ("SearchPage", "/search (Поиск)"),
    ("insight-chat/", "Ask AI (Чат «Спроси своих игроков»)"),
    ("insight_chat/", "Ask AI (Чат «Спроси своих игроков»)"),
    ("backend/app/corpus/", "Ask AI (Движок анализа)"),
    ("notifications/", "Telegram-уведомления и дайджесты"),
    ("zendesk", "Zendesk"),
    ("rollout-flags.env", "Rollout-флаги"),
    ("backend/app/cases/", "Cases (проверить флаг CASES_READ_PROJECT_IDS)"),
    ("CaseInbox", "Cases (проверить флаг CASES_READ_PROJECT_IDS)"),
    ("mcp_adapter/", "MCP (проверить флаг MCP_ENABLED)"),
    ("McpTokensCard", "MCP (проверить флаг MCP_ENABLED)"),
)

_FIX_VERB_RE = re.compile(r"^(?:fix|resolve|repair|prevent|avoid|stop|preserve|restore|keep|clarify|explain|hide|show|handle)\b", re.I)
_FEAT_VERB_RE = re.compile(r"^(?:add|enable|turn|allow|support|complete|build|create|introduce|implement|improve|speed up|reduce|adjust|use)\b", re.I)


@dataclass
class Commit:
    sha: str
    type: str
    scope: str | None
    subject: str
    breaking: bool = False
    body: str = ""
    files: tuple[str, ...] = ()
    surfaces: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.files, list):
            self.files = tuple(self.files)
        if isinstance(self.surfaces, list):
            self.surfaces = tuple(self.surfaces)


def _clean_body(raw_body: str, limit: int = 360) -> str:
    if not raw_body:
        return ""
    lines: list[str] = []
    for line in raw_body.splitlines():
        s = line.strip()
        if not s:
            continue
        low = s.lower()
        if low.startswith(("co-authored-by:", "(cherry picked from", "# conflicts:")):
            continue
        lines.append(s)
    text = " ".join(lines)
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def detect_surfaces(files: tuple[str, ...]) -> tuple[str, ...]:
    if not files:
        return ()
    found: list[str] = []
    for f in files:
        for needle, label in _SURFACE_RULES:
            if needle in f and label not in found:
                found.append(label)
    return tuple(found)


def is_non_product_file(path: str) -> bool:
    if path in _NON_PRODUCT_EXACT or path.endswith(".md"):
        return True
    if any(path.startswith(p) for p in _NON_PRODUCT_PREFIXES):
        return True
    if ".test." in path or "/tests/" in path or path.startswith("infra/docker-compose"):
        return True
    return False


def parse_commit(
    sha: str,
    message: str,
    feature_prefixes: tuple[str, ...] = (),
    *,
    files: tuple[str, ...] = (),
) -> Commit | None:
    lines = message.strip().splitlines()
    if not lines:
        return None
    first = lines[0].strip()
    if not first:
        return None
    body = _clean_body("\n".join(lines[1:]))
    surfaces = detect_surfaces(files)

    m = _CC.match(first)
    if m:
        ctype = m["type"].lower()
        scope = m["scope"].strip() if m["scope"] else None
        subject = m["subject"].strip()
        if ctype in RELEASE_TYPES or ctype in NON_RELEASE_TYPES:
            return Commit(sha, ctype, scope, subject, bool(m["breaking"]), body=body, files=files, surfaces=surfaces)
        # Check allowlisted one-word prefix before treating unknown cc type as scope
        for prefix in feature_prefixes:
            p = prefix.strip()
            if p and first[: len(p) + 1].lower() == (p + ":").lower():
                subj = first[len(p) + 1 :].strip()
                if subj:
                    return Commit(sha, "feat", p, subj, False, body=body, files=files, surfaces=surfaces)
        return Commit(sha, ctype, scope, subject, bool(m["breaking"]), body=body, files=files, surfaces=surfaces)

    for prefix in feature_prefixes:
        p = prefix.strip()
        if p and first[: len(p) + 1].lower() == (p + ":").lower():
            subj = first[len(p) + 1 :].strip()
            if subj:
                return Commit(sha, "feat", p, subj, False, body=body, files=files, surfaces=surfaces)

    tp = _TITLE_PREFIX.match(first)
    if tp:
        pref = tp["prefix"].strip()
        subj = tp["subject"].strip()
        if pref.lower() in {"cases prototype", "prototype"}:
            return Commit(sha, "prototype", pref, subj, False, body=body, files=files, surfaces=surfaces)
        return Commit(sha, "feat", pref, subj, False, body=body, files=files, surfaces=surfaces)

    if first.startswith("Merge "):
        return Commit(sha, "merge", None, first, False, body=body, files=files, surfaces=surfaces)

    if _FIX_VERB_RE.match(first):
        inferred = "fix"
    elif _FEAT_VERB_RE.match(first):
        inferred = "feat"
    else:
        inferred = "change"
    return Commit(sha, inferred, None, first, False, body=body, files=files, surfaces=surfaces)


def is_release_worthy(c: Commit) -> bool:
    if c.type in NON_RELEASE_TYPES:
        return False
    if c.scope and c.scope.lower() in NOISE_SCOPES:
        return False
    if _NOISE_SUBJECT_RE.match(c.subject):
        return False
    if c.type == "merge" and not c.body:
        # Keep a feature-branch merge without body if it names a feature/change,
        # otherwise branch commits carry the details.
        low = c.subject.lower()
        if not (
            low.startswith("merge feat/")
            or any(k in low for k in ("export", "attention", "invitations", "facet", "zendesk", "player", "autoanswer", "replies", "alerts", "cases", "corpus", "engine"))
        ):
            return False
    if c.files and all(is_non_product_file(f) for f in c.files):
        return False
    return True


def is_feature_commit(c: Commit) -> bool:
    return c.type in {"feat", "perf", "change"}


_INTERNAL_DETAIL_RE = re.compile(
    r"\b(?:reconciliation|linker|shadow|backfill|migration|deadlock|heartbeat|"
    r"fixture|rehearsal|sampling|allowlist|certificate|telemetry|eval)\b",
    re.IGNORECASE,
)

_HIGH_VOLUME_INTERNAL_SCOPES = {"corpus", "ai-chat", "cases", "analyst", "chat-jobs", "chat-worker"}


def prioritize_for_enrichment(commits: list[Commit], *, limit: int = 90) -> list[Commit]:
    """Pick the most promising candidates to fetch changed-file lists for."""
    if len(commits) <= limit:
        return commits
    tier0: list[Commit] = []
    tier1: list[Commit] = []
    tier2: list[Commit] = []
    for c in reversed(commits):  # prefer newest commits within each tier
        scope_low = (c.scope or "").lower()
        is_internal = (
            scope_low in _HIGH_VOLUME_INTERNAL_SCOPES
            or bool(_INTERNAL_SUBTASK_RE.search(c.subject))
            or bool(_INTERNAL_DETAIL_RE.search(c.subject))
        )
        if not is_internal:
            tier0.append(c)
        elif c.type == "feat" and not _INTERNAL_SUBTASK_RE.search(c.subject):
            tier1.append(c)
        else:
            tier2.append(c)
    return (tier0 + tier1 + tier2)[:limit]


def _is_ui_or_api(c: Commit) -> bool:
    return any(
        "/pages/" in f or "/components/" in f or "/api/routes/" in f or "rollout-flags.env" in f
        for f in c.files
    )


def collapse_commits(commits: list[Commit], *, scope_threshold: int = 12, max_total: int = 75) -> list[Commit]:
    """Collapse noisy high-volume scopes and mega-branches so real user changes stay visible."""
    if len(commits) <= scope_threshold:
        return commits

    by_scope: dict[str, list[Commit]] = defaultdict(list)
    for c in commits:
        key = (c.scope or "-").lower()
        by_scope[key].append(c)

    collapsed_scopes = {k for k, grp in by_scope.items() if k != "-" and len(grp) > scope_threshold}
    out: list[Commit] = []
    seen_summary_for: set[str] = set()

    for c in commits:
        key = (c.scope or "-").lower()
        if key not in collapsed_scopes:
            out.append(c)
            continue
        grp = by_scope[key]
        # Keep UI/API-touching or non-subtask feat commits up to a small cap
        if key not in seen_summary_for:
            seen_summary_for.add(key)
            highlights = [
                x for x in grp
                if (_is_ui_or_api(x) or (x.type == "feat" and not _INTERNAL_SUBTASK_RE.search(x.subject)))
            ]
            if not highlights:
                highlights = [x for x in grp if x.type == "feat"] or grp[:5]
            picked = highlights[:8]
            out.extend(picked)
            remaining = len(grp) - len(picked)
            if remaining > 0:
                sample = "; ".join(x.subject for x in grp if x not in picked)[:320]
                out.append(Commit(
                    sha=grp[-1].sha,
                    type="feat",
                    scope=grp[0].scope,
                    subject=f"Ещё {remaining} внутренних/мелких коммитов в scope {grp[0].scope}",
                    body=sample,
                ))

    if len(out) <= max_total:
        return out

    # Mega-release safety cap: separate into priority tiers and prefer the NEWEST
    # commits within a tier (compare order is oldest-first) so recent main work
    # is never evicted by a huge older feature branch.
    tiers: list[list[tuple[int, Commit]]] = [[], [], [], []]
    for idx, c in enumerate(out):
        is_internal = bool(_INTERNAL_SUBTASK_RE.search(c.subject) or _INTERNAL_DETAIL_RE.search(c.subject))
        if _is_ui_or_api(c):
            tiers[0].append((idx, c))
        elif c.files and not is_internal:
            tiers[1].append((idx, c))
        elif c.type in {"feat", "fix", "perf"} and not is_internal:
            tiers[2].append((idx, c))
        else:
            tiers[3].append((idx, c))

    chosen: list[tuple[int, Commit]] = []
    dropped: list[Commit] = []
    remaining = max_total
    for tier in tiers:
        if remaining <= 0:
            dropped.extend(c for _, c in tier)
        elif len(tier) <= remaining:
            chosen.extend(tier)
            remaining -= len(tier)
        else:
            chosen.extend(tier[-remaining:])  # keep newest commits in this tier
            dropped.extend(c for _, c in tier[:-remaining])
            remaining = 0

    chosen.sort(key=lambda pair: pair[0])
    result = [c for _, c in chosen]
    if dropped:
        result.append(Commit(
            sha=dropped[-1].sha,
            type="feat",
            scope="summary",
            subject=f"Ещё {len(dropped)} более ранних/внутренних коммитов в диапазоне",
            body="; ".join(x.subject for x in dropped[-8:])[:320],
        ))
    return result


def filter_commits(
    raw: list[tuple[str, str]],
    feature_prefixes: tuple[str, ...] = (),
    *,
    files_by_sha: dict[str, tuple[str, ...]] | None = None,
) -> list[Commit]:
    out: list[Commit] = []
    fmap = files_by_sha or {}
    for sha, message in raw:
        c = parse_commit(sha, message, feature_prefixes, files=fmap.get(sha, ()))
        if c and is_release_worthy(c):
            out.append(c)
    return out
