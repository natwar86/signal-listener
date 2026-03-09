"""
AI classification layer using Claude Haiku.

Classifies each signal for sentiment, urgency, pain types,
competitor mentions, market, and intent.
"""

import json
import logging
from typing import Optional

from config import ANTHROPIC_API_KEY, CLASSIFICATION_MODEL, SALTBOX_MARKETS

log = logging.getLogger("signal-listener")

CLASSIFICATION_PROMPT = """You are classifying a signal for Saltbox, a co-warehousing and fulfillment company for ecommerce brands. Saltbox has 12 locations across the U.S. (Atlanta, Dallas, Denver, Houston, Austin, Nashville, Charlotte, Chicago, Salt Lake City, Phoenix, San Antonio).

Signal source: {source}
Content: {content}
Rating (if applicable): {rating}
Author/Company: {author}
Location: {location}

Classify this signal. Return ONLY a JSON object with these fields:

{{
  "sentiment": "negative" or "neutral" or "positive",
  "urgency": "hot" (actively looking to switch/buy now) or "warm" (frustrated but not switching yet) or "cold" (general discussion),
  "pain_types": array of applicable types from ["cost", "control", "service_quality", "scale", "speed", "customization"],
  "competitors_mentioned": array of any 3PL/warehouse/shipping companies mentioned (e.g. ["ShipBob", "ShipMonk"]),
  "market": closest Saltbox market if a city/state is mentioned, otherwise "unknown". Valid: {markets},
  "intent": one of "switching", "exploring", "complaining", "asking", "hiring", "scaling",
  "summary": one sentence summarizing what this person needs or is saying
}}"""


def classify_signal(signal_dict: dict) -> Optional[dict]:
    """
    Classify a single signal using Claude Haiku.
    Returns the classification dict, or None on failure.
    """
    if not ANTHROPIC_API_KEY:
        log.error("ANTHROPIC_API_KEY not set. Cannot classify.")
        return None

    try:
        import anthropic
    except ImportError:
        log.error("anthropic package not installed. Run: pip install anthropic")
        return None

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    raw = json.loads(signal_dict.get("raw_json", "{}")) if isinstance(signal_dict.get("raw_json"), str) else signal_dict
    metadata = raw.get("metadata", {})

    prompt = CLASSIFICATION_PROMPT.format(
        source=signal_dict.get("source", "unknown"),
        content=signal_dict.get("content_body", "")[:2000],
        rating=signal_dict.get("content_rating", "N/A"),
        author=signal_dict.get("author_name", "unknown"),
        location=metadata.get("location", "unknown"),
        markets=", ".join(SALTBOX_MARKETS),
    )

    try:
        message = client.messages.create(
            model=CLASSIFICATION_MODEL,
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )

        response_text = message.content[0].text.strip()

        # Extract JSON from response (handle markdown code blocks)
        if "```" in response_text:
            json_str = response_text.split("```")[1]
            if json_str.startswith("json"):
                json_str = json_str[4:]
            json_str = json_str.strip()
        else:
            json_str = response_text

        classification = json.loads(json_str)

        # Validate fields
        classification.setdefault("sentiment", "neutral")
        classification.setdefault("urgency", "cold")
        classification.setdefault("pain_types", [])
        classification.setdefault("competitors_mentioned", [])
        classification.setdefault("market", "unknown")
        classification.setdefault("intent", "complaining")
        classification.setdefault("summary", "")

        return classification

    except json.JSONDecodeError as e:
        log.error(f"Failed to parse classification JSON: {e}")
        log.debug(f"Raw response: {response_text}")
        return None
    except Exception as e:
        log.error(f"Classification API error: {e}")
        return None


def classify_batch(signals: list[dict], batch_size: int = 10) -> list[tuple[str, dict]]:
    """
    Classify a batch of signals. Returns list of (signal_id, classification) tuples.
    Processes one at a time (Haiku is fast enough, and we want per-signal error handling).
    """
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
