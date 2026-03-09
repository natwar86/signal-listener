"""
Common signal schema for all data sources.

Every signal — whether from Shopify reviews, Reddit, Google Maps, etc. —
gets normalized to this structure before storage and display.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional


VALID_SOURCES = {
    "shopify_reviews", "reddit", "google_maps", "job_postings",
    "crunchbase", "twitter", "amazon_forums", "trustpilot",
    "bbb", "linkedin", "macro", "shopify_community", "youtube",
}

VALID_SENTIMENTS = {"negative", "neutral", "positive"}
VALID_URGENCIES = {"hot", "warm", "cold"}
VALID_PAIN_TYPES = {"cost", "control", "service_quality", "scale", "speed", "customization"}
VALID_INTENTS = {"switching", "exploring", "complaining", "asking", "hiring", "scaling"}


@dataclass
class Author:
    name: Optional[str] = None
    profile_url: Optional[str] = None
    company: Optional[str] = None
    company_url: Optional[str] = None


@dataclass
class Content:
    title: Optional[str] = None
    body: str = ""
    rating: Optional[float] = None


@dataclass
class Classification:
    sentiment: Optional[str] = None          # negative / neutral / positive
    urgency: Optional[str] = None            # hot / warm / cold
    pain_types: list[str] = field(default_factory=list)
    competitors_mentioned: list[str] = field(default_factory=list)
    market: Optional[str] = None             # one of SALTBOX_MARKETS or "unknown"
    intent: Optional[str] = None             # switching / exploring / etc.
    summary: Optional[str] = None            # AI-generated one-liner


@dataclass
class Signal:
    id: str = ""
    source: str = ""
    source_url: str = ""
    timestamp: str = ""         # ISO 8601
    collected_at: str = ""      # ISO 8601

    author: Author = field(default_factory=Author)
    content: Content = field(default_factory=Content)
    classification: Classification = field(default_factory=Classification)

    # Extra metadata (source-specific fields that don't fit the common schema)
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.id:
            self.id = str(uuid.uuid4())
        if not self.collected_at:
            self.collected_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> Signal:
        sig = cls(
            id=d.get("id", ""),
            source=d.get("source", ""),
            source_url=d.get("source_url", ""),
            timestamp=d.get("timestamp", ""),
            collected_at=d.get("collected_at", ""),
            metadata=d.get("metadata", {}),
        )
        a = d.get("author", {})
        sig.author = Author(
            name=a.get("name"),
            profile_url=a.get("profile_url"),
            company=a.get("company"),
            company_url=a.get("company_url"),
        )
        c = d.get("content", {})
        sig.content = Content(
            title=c.get("title"),
            body=c.get("body", ""),
            rating=c.get("rating"),
        )
        cl = d.get("classification", {})
        sig.classification = Classification(
            sentiment=cl.get("sentiment"),
            urgency=cl.get("urgency"),
            pain_types=cl.get("pain_types", []),
            competitors_mentioned=cl.get("competitors_mentioned", []),
            market=cl.get("market"),
            intent=cl.get("intent"),
            summary=cl.get("summary"),
        )
        return sig
