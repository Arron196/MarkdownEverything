from collections.abc import Callable

from app.converters.web_extractors import bilibili, discourse, nodeseek, wikipedia
from app.converters.web_extractors.base import WebExtractorContext, WebExtractorResult
from app.converters.web_extractors.snapshot import extract as snapshot_extract

Extractor = Callable[[WebExtractorContext], WebExtractorResult | None]

SPECIALIZED_EXTRACTORS: list[Extractor] = [
    bilibili.extract,
    discourse.extract,
    nodeseek.extract,
    wikipedia.extract,
]

SNAPSHOT_EXTRACTOR: Extractor = snapshot_extract


def run_specialized_extractors(context: WebExtractorContext) -> WebExtractorResult | None:
    results = []
    for extractor in SPECIALIZED_EXTRACTORS:
        result = extractor(context)
        if result and result.body.strip():
            results.append(result)
    if not results:
        return None
    return max(results, key=lambda item: item.score)
