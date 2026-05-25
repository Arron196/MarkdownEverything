from dataclasses import dataclass, field


@dataclass
class ConversionResult:
    title: str
    source_type: str
    body: str = ""
    summary_seed: str = ""
    source_url: str | None = None
    author: str | None = None
    created_at: str | None = None
    resources: list[str] = field(default_factory=list)
    language: str | None = None
    duration: str | None = None
    timeline: list[dict[str, str]] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

