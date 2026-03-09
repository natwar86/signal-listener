"""
Configuration for the Signal Listener.

API keys are loaded from environment variables or a .env file.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

ROOT_DIR = Path(__file__).parent
DB_PATH = ROOT_DIR / "signals.db"
DASHBOARD_DATA_DIR = ROOT_DIR / "docs" / "data"
OUTPUT_DIR = ROOT_DIR / "output"

# ---------------------------------------------------------------------------
# API Keys
# ---------------------------------------------------------------------------

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET", "")
REDDIT_USER_AGENT = os.getenv("REDDIT_USER_AGENT", "signal-listener/0.2 by saltbox")

# ---------------------------------------------------------------------------
# Shopify Scraper
# ---------------------------------------------------------------------------

SHOPIFY_APPS = [
    "shipbob",
    "shiphero",
    "shipmonk",
    "fulfillrite",
    "saltbox",  # Parsel (own app)
]

SHOPIFY_MIN_DELAY = 4.0
SHOPIFY_MAX_DELAY = 8.0

# ---------------------------------------------------------------------------
# Reddit
# ---------------------------------------------------------------------------

REDDIT_SUBREDDITS = [
    "ecommerce",
    "FulfillmentByAmazon",
    "smallbusiness",
    "Entrepreneur",
    "shopify",
    "ecommerceseller",
    "AmazonSeller",
]

REDDIT_KEYWORDS = [
    "warehouse space", "co-warehousing", "shared warehouse", "ecommerce warehouse",
    "ShipBob", "ShipMonk", "ShipHero", "Flexport", "3PL",
    "leaving my 3PL", "unhappy with fulfillment", "need warehouse", "outgrowing",
    "scaling fulfillment", "FBA prep", "prep center", "FBA alternative",
    "fulfillment options", "which 3PL", "warehouse recommendation",
    "self-fulfillment vs 3PL",
]

# ---------------------------------------------------------------------------
# AI Classification
# ---------------------------------------------------------------------------

CLASSIFICATION_MODEL = "claude-haiku-4-5-20251001"

SALTBOX_MARKETS = [
    "atlanta", "dallas", "denver", "houston", "austin",
    "nashville", "charlotte", "chicago", "salt_lake_city",
    "phoenix", "san_antonio",
]

COMPETITORS = {
    "3pl": ["ShipBob", "ShipMonk", "ShipHero", "Flexport", "Flowspace", "Fulfillrite"],
    "cowarehouse": ["ReadySpaces", "Cubework", "WareSpace", "Portal Warehousing", "Loloft", "FlexEtc"],
    "shipping": ["ShipStation", "Shippo", "Pirate Ship", "Easyship", "Veeqo"],
}
