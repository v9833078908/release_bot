from dataclasses import dataclass, field


@dataclass
class Post:
    intro: str
    features: list[str] = field(default_factory=list)
    improvements: list[str] = field(default_factory=list)
    fixes_summary: str | None = None
