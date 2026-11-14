from __future__ import annotations

from .base import BriefRequest, BriefResult


class StubRunner:
    """Fixed, schema-valid output. For tests and local end-to-end runs; spends no quota."""

    def __init__(self, *, insufficient_topic_ids: frozenset[str] = frozenset()) -> None:
        self.insufficient = insufficient_topic_ids
        self.calls = 0

    async def run(self, request: BriefRequest) -> BriefResult:
        self.calls += 1
        sections = []
        for topic in request["topics"]:
            if topic["topic_id"] in self.insufficient:
                items: list[dict] = []
                status, note = "insufficient_news", "Not enough fresh news."
            else:
                status, note = "ok", None
                items = [
                    {
                        "rank": n,
                        "title": f"{topic['title'][:180]} item {n}",
                        "summary": "Fixed stub summary.",
                        "source_name": "Example News",
                        "source_url": f"https://news{n}.example.com/{n}",
                        "published_at": f"{request['report_date']}T00:00:00Z",
                    }
                    for n in range(1, topic["item_count"] + 1)
                ]
            section = {
                "topic_id": topic["topic_id"],
                "title": topic["title"],
                "status": status,
                "items": items,
            }
            if note:
                section["note"] = note
            sections.append(section)
        return {
            "report_date": request["report_date"],
            "locale": request["locale"],
            "sections": sections,
            "usage": {
                "runner": "stub",
                "model": "stub",
                "duration_ms": 0,
                "search_count": 0,
                "input_tokens": None,
                "output_tokens": None,
            },
        }
