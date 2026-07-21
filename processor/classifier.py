"""
AI classification layer using Claude Haiku via OpenRouter.

Vendor-neutral: classifies signals for a competitive-intelligence feed
covering the ecommerce fulfillment ecosystem, not for any one company.

OpenRouter (openrouter.ai) fronts the model so billing isn't tied to a
single provider — CLASSIFICATION_MODELS lists the primary model plus
automatic failover targets, all reachable through one API key.

Structured output via response_format json_schema guarantees valid JSON —
no markdown fence parsing. Short glowing reviews skip the API entirely
(cheap path) since they carry no competitive signal.
"""

import re
import json
import logging
from typing import Optional

from config import (
    OPENROUTER_API_KEY, CLASSIFICATION_MODELS, US_METROS,
    COMPETITORS, SHOPIFY_APP_NAMES,
)

log = logging.getLogger("signal-listener")

CLASSIFICATION_PROMPT = """You classify customer reviews for a competitive-intelligence feed covering the ecommerce fulfillment ecosystem: 3PLs, co-warehousing operators, shipping software, and FBA prep services. Subscribers are competitors of the reviewed companies looking for merchants unhappy enough to switch providers, and for patterns in what customers praise or complain about.

Reviewed company: {subject}
Signal source: {source}
Rating (if applicable): {rating}
Reviewer (usually the merchant/store name): {author}
Reviewer location: {location}
Review content:
{content}

Classification guidance:
- urgency "hot" = actively switching, cancelling, or looking for alternatives right now; "warm" = clear frustration or unresolved pain but not leaving yet; "cold" = neutral, positive, or general discussion.
- competitors_mentioned = companies OTHER than the reviewed company mentioned by name (e.g. a ShipBob review saying "switching to ShipMonk" mentions ShipMonk).
- market = nearest major US metro if a US city/region is stated or clearly implied; "other_us" for US locations far from any listed metro; "non_us" for non-US locations; "unknown" if no location signal.
- summary = one sentence on what this reviewer needs or is saying."""

CLASSIFICATION_SCHEMA = {
    "type": "object",
    "properties": {
        "sentiment": {"type": "string", "enum": ["negative", "neutral", "positive"]},
        "urgency": {"type": "string", "enum": ["hot", "warm", "cold"]},
        "pain_types": {
            "type": "array",
            "items": {"type": "string",
                      "enum": ["cost", "control", "service_quality",
                               "scale", "speed", "customization"]},
        },
        "competitors_mentioned": {"type": "array", "items": {"type": "string"}},
        "market": {"type": "string", "enum": US_METROS + ["unknown"]},
        "intent": {"type": "string",
                   "enum": ["switching", "exploring", "complaining", "asking",
                            "hiring", "scaling", "praising"]},
        "summary": {"type": "string"},
    },
    "required": ["sentiment", "urgency", "pain_types", "competitors_mentioned",
                 "market", "intent", "summary"],
    "additionalProperties": False,
}

# Brand names whose presence in a review means it carries competitive signal
# even when glowing — so it must NOT take the cheap path.
_ALL_BRANDS = sorted(
    {b for brands in COMPETITORS.values() for b in brands}
    | set(SHOPIFY_APP_NAMES.values()),
    key=len, reverse=True,
)
_BRAND_RE = re.compile("|".join(re.escape(b) for b in _ALL_BRANDS), re.IGNORECASE)

_client = None


def _get_client():
    global _client
    if _client is None:
        import openai
        _client = openai.OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=OPENROUTER_API_KEY,
        )
    return _client


def _subject_of(signal_dict: dict, metadata: dict) -> str:
    """Which company is this review about?"""
    source = signal_dict.get("source", "")
    if source == "shopify_reviews":
        slug = metadata.get("app_slug", "")
        return SHOPIFY_APP_NAMES.get(slug, slug or "unknown")
    if source == "google_maps":
        return metadata.get("place_brand") or metadata.get("place_name") or "unknown"
    if source in ("trustpilot", "g2", "capterra"):
        return metadata.get("company_brand") or metadata.get("company_domain") or "unknown"
    return "unknown"


def _cheap_classify(body: str, rating, subject: str) -> Optional[dict]:
    """Short glowing reviews with no other company named carry no competitive
    signal — classify without an API call. Returns None if the review needs
    the model."""
    if rating is None or rating < 5:
        return None
    if len(body) >= 200:
        return None
    if _BRAND_RE.search(body):
        return None
    return {
        "sentiment": "positive",
        "urgency": "cold",
        "pain_types": [],
        "competitors_mentioned": [],
        "market": "unknown",
        "intent": "praising",
        "summary": f"Positive review of {subject}: {body[:120]}".strip(),
    }


def classify_signal(signal_dict: dict) -> Optional[dict]:
    """Classify a single signal. Returns the classification dict, or None on failure."""
    if not OPENROUTER_API_KEY:
        log.error("OPENROUTER_API_KEY not set. Cannot classify.")
        return None

    raw = (json.loads(signal_dict["raw_json"])
           if isinstance(signal_dict.get("raw_json"), str)
           else signal_dict)
    metadata = raw.get("metadata", {})
    subject = _subject_of(signal_dict, metadata)
    body = signal_dict.get("content_body", "") or ""
    rating = signal_dict.get("content_rating")

    cheap = _cheap_classify(body, rating, subject)
    if cheap is not None:
        return cheap

    prompt = CLASSIFICATION_PROMPT.format(
        subject=subject,
        source=signal_dict.get("source", "unknown"),
        content=body[:2000],
        rating=rating if rating is not None else "N/A",
        author=signal_dict.get("author_name", "unknown"),
        location=metadata.get("location", "unknown"),
    )

    try:
        response = _get_client().chat.completions.create(
            model=CLASSIFICATION_MODELS[0],
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_schema",
                             "json_schema": {"name": "classification",
                                             "strict": True,
                                             "schema": CLASSIFICATION_SCHEMA}},
            # OpenRouter routing: try each model in order until one answers
            extra_body={"models": CLASSIFICATION_MODELS},
        )
        text = response.choices[0].message.content
        if not text:
            log.error(f"Empty classification response "
                      f"(finish_reason={response.choices[0].finish_reason})")
            return None
        return json.loads(text)
    except Exception as e:
        log.error(f"Classification API error: {e}")
        return None


def classify_batch(signals: list[dict], batch_size: int = 10) -> list[tuple[str, dict]]:
    """Classify a batch of signals. Returns list of (signal_id, classification) tuples."""
    results = []
    for i, signal in enumerate(signals, 1):
        signal_id = signal["id"]
        log.info(f"Classifying {i}/{len(signals)}: {signal_id}")
        classification = classify_signal(signal)
        if classification:
            results.append((signal_id, classification))
        else:
            log.warning(f"Failed to classify {signal_id}")
    return results
