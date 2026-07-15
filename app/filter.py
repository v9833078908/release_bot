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


def parse_commit(sha: str, message: str) -> Commit | None:
    lines = message.strip().splitlines()
    if not lines:
        return None
    m = _CC.match(lines[0].strip())
    if not m:
        return None
    return Commit(sha, m["type"], m["scope"], m["subject"].strip(), bool(m["breaking"]))


def is_release_worthy(c: Commit) -> bool:
    if c.type not in RELEASE_TYPES:
        return False
    if c.scope and c.scope in NOISE_SCOPES:
        return False
    return True


def filter_commits(raw: list[tuple[str, str]]) -> list[Commit]:
    out: list[Commit] = []
    for sha, message in raw:
        c = parse_commit(sha, message)
        if c and is_release_worthy(c):
            out.append(c)
    return out
