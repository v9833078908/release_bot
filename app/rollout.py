import re

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)

# Flags relevant to understanding what customers actually see in Game Pulse.
_TRACKED_PREFIXES = (
    "MCP_",
    "CASES_",
    "MESSAGE_SEMANTICS_",
    "CORPUS_",
    "INSIGHT_CHAT_",
    "DATA_AGENT_",
    "FINAL_EVENT_",
    "AUTOANSWER_",
    "SUPPORT_DRAFTS_",
    "TOPICS_FACETS_",
    "FACET_",
    "CHAT_MODERATION_",
    "GAME_METRICS_",
    "EMAIL_ALERT_",
    "BILLING_",
    "VITALS_",
    "ISSUE_RESOLUTION_",
)


def parse_env_flags(text: str | None) -> dict[str, str]:
    """Parse KEY=VALUE pairs from infra/rollout-flags.env, ignoring comments."""
    if not text:
        return {}
    flags: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if key:
            flags[key] = val.strip()
    return flags


def summarize_flag_value(val: str) -> str:
    """Summarize a rollout flag value without exposing UUIDs or project names."""
    v = val.strip()
    if not v:
        return "OFF (empty)"
    low = v.lower()
    if low == "false":
        return "OFF (false)"
    if low == "true":
        return "ON (true)"
    if v == "*":
        return "ALL projects (*)"
    parts = [p.strip() for p in v.split(",") if p.strip()]
    if parts and all(_UUID_RE.match(p) for p in parts):
        return f"canary ({len(parts)} project{'s' if len(parts) != 1 else ''})"
    if v.startswith("[") or v.startswith("{"):
        return "configured"
    if len(v) > 40:
        return "configured"
    return v


def _is_relevant(key: str) -> bool:
    return key.startswith(_TRACKED_PREFIXES)


def build_rollout_context(base_text: str | None, head_text: str | None) -> str:
    """Summarize changed and active/off rollout flags for the release-notes prompt."""
    head = parse_env_flags(head_text)
    if not head:
        return ""
    base = parse_env_flags(base_text)

    changes: list[str] = []
    if base:
        for k in sorted(set(base) | set(head)):
            if not _is_relevant(k):
                continue
            b_val = base.get(k)
            h_val = head.get(k)
            if b_val == h_val:
                continue
            if b_val is None:
                changes.append(f"- {k}: added = {summarize_flag_value(h_val or '')}")
            elif h_val is None:
                if summarize_flag_value(b_val) != "ALL projects (*)":
                    changes.append(f"- {k}: retired from flags (now default/always-on in code)")
            else:
                changes.append(
                    f"- {k}: {summarize_flag_value(b_val)} -> {summarize_flag_value(h_val)}"
                )

    disabled: list[str] = []
    canary: list[str] = []
    for k in sorted(head):
        if not _is_relevant(k):
            continue
        summary = summarize_flag_value(head[k])
        if summary.startswith("OFF"):
            disabled.append(f"- {k}: {summary}")
        elif summary.startswith("canary"):
            canary.append(f"- {k}: {summary}")

    sections: list[str] = []
    if changes:
        sections.append("Flags changed in this release:\n" + "\n".join(changes))
    if disabled:
        sections.append("Flags currently OFF in rollout-flags.env:\n" + "\n".join(disabled))
    if canary:
        sections.append("Flags currently canary-only (limited projects):\n" + "\n".join(canary))
    return "\n\n".join(sections)
