from dataclasses import dataclass, field


@dataclass
class TitleCandidate:
    value: str
    source: str
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)


@dataclass
class PageAnalysis:
    url: str
    title_candidates: list[TitleCandidate]
    metadata: dict
    blocked_reason: str | None
    visible_text_length: int
    dom_size: int
    dom_has_spa_signature: bool = False
    page_kind: str = "article"

    @property
    def title(self) -> str:
        if not self.title_candidates:
            return "Untitled"
        return max(self.title_candidates, key=lambda item: item.score).value


@dataclass
class CandidateMetrics:
    L: int = 0
    P: int = 0
    H: int = 0
    LI: int = 0
    TB: int = 0
    CB: int = 0
    IC: int = 0
    IM: int = 0
    BQ: int = 0
    A: int = 0
    rho: float = 0.0
    U: float = 1.0
    N: int = 0
    R: float = 0.0
    TS: float = 0.0
    MF: float = 0.0
    noise_cost: float = 0.0
    duplication_cost: float = 0.0


@dataclass
class ExtractionCandidate:
    name: str
    source: str
    node: object | None
    markdown: str
    metrics: CandidateMetrics = field(default_factory=CandidateMetrics)
    score: float = 0.0
    quality_status: str = "weak"
    quality_reasons: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    dom_depth: int = 9999
    source_score: float = 0.0
    hard_rejected: bool = False

