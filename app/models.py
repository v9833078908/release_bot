from dataclasses import dataclass, field


@dataclass
class Theme:
    title: str
    body: str


@dataclass
class Post:
    themes: list[Theme] = field(default_factory=list)
    fixes: list[str] = field(default_factory=list)
