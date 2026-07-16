import re
from dataclasses import dataclass

_CC = re.compile(
    r"^(?P<type>\w+)(?:\((?P<scope>[^)]+)\))?(?P<breaking>!)?:\s*(?P<subject>.+)$"
)

RELEASE_TYPES = {"feat", "fix", "perf"}
NOISE_SCOPES = {"research", "litellm", "plan", "plans", "docs"}


@dataclass
class Commit:
    sha: str
    type: str
    scope: str | None
    subject: str
    breaking: bool


def parse_commit(sha: str, message: str, feature_prefixes: tuple[str, ...] = ()) -> Commit | None:
    lines = message.strip().splitlines()
    if not lines:
        return None
    first = lines[0].strip()
    m = _CC.match(first)
    conv = None
    if m:
        conv = Commit(sha, m["type"], m["scope"], m["subject"].strip(), bool(m["breaking"]))
        if conv.type in RELEASE_TYPES:
            return conv                      # real conventional release commit wins
    for prefix in feature_prefixes:          # else: explicit allowlist promotion
        p = prefix.strip()
        if p and first[: len(p) + 1].lower() == (p + ":").lower():
            subject = first[len(p) + 1:].strip()
            if subject:
                return Commit(sha, "feat", p, subject, False)
    return conv                              # non-release conventional (dropped later) or None


def is_release_worthy(c: Commit) -> bool:
    if c.type not in RELEASE_TYPES:
        return False
    if c.scope and c.scope in NOISE_SCOPES:
        return False
    return True


def filter_commits(raw: list[tuple[str, str]], feature_prefixes: tuple[str, ...] = ()) -> list[Commit]:
    out: list[Commit] = []
    for sha, message in raw:
        c = parse_commit(sha, message, feature_prefixes)
        if c and is_release_worthy(c):
            out.append(c)
    return out
