from dataclasses import dataclass, field

from bs4 import BeautifulSoup


@dataclass
class WebExtractorContext:
    soup: BeautifulSoup
    base_url: str
    title: str
    rendered: bool = False
    metadata: dict = field(default_factory=dict)


@dataclass
class WebExtractorResult:
    name: str
    body: str
    score: float = 0
    metadata: dict = field(default_factory=dict)
