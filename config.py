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

# Railway mounts a persistent volume at VOLUME_PATH.
# Locally, everything stays in the project directory.
ROOT_DIR = Path(__file__).parent
VOLUME_PATH = Path(os.getenv("VOLUME_PATH", str(ROOT_DIR)))
DB_PATH = VOLUME_PATH / "signals.db"
DASHBOARD_DATA_DIR = VOLUME_PATH / "docs" / "data"
OUTPUT_DIR = VOLUME_PATH / "output"

# ---------------------------------------------------------------------------
# API Keys
# ---------------------------------------------------------------------------

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
EXA_API_KEY = os.getenv("EXA_API_KEY", "")
APOLLO_API_KEY = os.getenv("APOLLO_API_KEY", "")
APIFY_API_TOKEN = os.getenv("APIFY_API_TOKEN", "")
REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET", "")
REDDIT_USER_AGENT = os.getenv("REDDIT_USER_AGENT", "signal-listener/0.2 by saltbox")

# ---------------------------------------------------------------------------
# Shopify Scraper
# ---------------------------------------------------------------------------

SHOPIFY_APPS = [
    # 3PLs (highest signal value for WareSpace referrals)
    "shipbob",
    "shiphero",
    "shipmonk",
    "fulfillrite-order-fulfillment",  # Fulfillrite
    # "saltbox" — Parsel, too few reviews for /reviews page
    # "deliverr-fulfillment-1" — Flexport, delisted from app store
    # "app29385" — Red Stag, no parseable reviews
    # Shipping/logistics (adjacent pain)
    "shipstation",
    "easyship",
    # WMS/inventory (warehouse need signal)
    "skusavvy",
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

# ---------------------------------------------------------------------------
# Google Maps (Apify compass/google-maps-reviews-scraper)
# ---------------------------------------------------------------------------

APIFY_REVIEWS_ACTOR = "compass/google-maps-reviews-scraper"

# Pay-per-event pricing (USD) — used for cost estimation guard rails.
# Source: https://apify.com/compass/google-maps-reviews-scraper
APIFY_COST_PER_RUN = 0.007
APIFY_COST_PER_PLACE = 0.004
APIFY_COST_PER_REVIEW = 0.0005

# Target places. Each entry: {"name": str, "url": str}
# URLs use Google Maps search format with company name + city/address —
# the Apify reviews actor resolves these to the correct place.
#
# Curation principle (validated 2026-04-07): focus on businesses where
# customers physically visit, so reviews carry real signal:
#   - co-warehouses (customers rent space, walk in daily)
#   - HQ-anchored 3PLs like ShipMonk where the brand maps to a location
#   - FBA prep centers (sellers ship in and sometimes visit)
# Avoid 3PL fulfillment-only warehouse addresses — they get sparse,
# noisy reviews because customers interact digitally, not physically.
GOOGLE_MAPS_PLACES: list[dict] = [
    # --- Co-warehouses (highest signal) ---
    {"name": "Saltbox - Atlanta Upper Westside",
     "url": "https://www.google.com/maps/search/Saltbox+1345+Seaboard+Industrial+Blvd+NW+Atlanta+GA+30318"},
    {"name": "Saltbox - Atlanta Westside Park",
     "url": "https://www.google.com/maps/search/Saltbox+1314+Chattahoochee+Ave+NW+Atlanta+GA+30318"},
    {"name": "Saltbox - Dallas",
     "url": "https://www.google.com/maps/search/Saltbox+coworking+warehouse+Dallas+TX"},
    {"name": "Saltbox - Denver",
     "url": "https://www.google.com/maps/search/Saltbox+coworking+warehouse+Denver+CO"},
    {"name": "Saltbox - Nashville",
     "url": "https://www.google.com/maps/search/Saltbox+coworking+warehouse+Nashville+TN"},
    {"name": "Saltbox - Charlotte",
     "url": "https://www.google.com/maps/search/Saltbox+coworking+warehouse+Charlotte+NC"},
    {"name": "ReadySpaces - Houston",
     "url": "https://www.google.com/maps/search/ReadySpaces+warehouse+Houston+TX"},
    {"name": "ReadySpaces - Phoenix",
     "url": "https://www.google.com/maps/search/ReadySpaces+warehouse+Phoenix+AZ"},
    {"name": "Cubework - Los Angeles",
     "url": "https://www.google.com/maps/search/Cubework+warehouse+Los+Angeles+CA"},
    {"name": "Cubework - Chicago",
     "url": "https://www.google.com/maps/search/Cubework+warehouse+Chicago+IL"},
    {"name": "WareSpace - Austin",
     "url": "https://www.google.com/maps/search/WareSpace+warehouse+Austin+TX"},
    {"name": "Portal Warehousing - Brooklyn",
     "url": "https://www.google.com/maps/search/Portal+Warehousing+Brooklyn+NY"},

    # --- ShipMonk (HQ-anchored 3PL, proven high signal density) ---
    {"name": "ShipMonk - Fort Lauderdale, FL",
     "url": "https://www.google.com/maps/search/ShipMonk+201+NW+22nd+Ave+Fort+Lauderdale+FL+33311"},
    {"name": "ShipMonk - Las Vegas, NV",
     "url": "https://www.google.com/maps/search/ShipMonk+fulfillment+Las+Vegas+NV"},
    {"name": "ShipMonk - Pittston, PA",
     "url": "https://www.google.com/maps/search/ShipMonk+fulfillment+Pittston+PA"},

    # --- FBA prep centers (sellers ship in and visit) ---
    {"name": "FBA Inspection - Riverside, CA",
     "url": "https://www.google.com/maps/search/FBA+Inspection+Riverside+CA"},
    {"name": "McKenzie Services - Spartanburg, SC",
     "url": "https://www.google.com/maps/search/McKenzie+Services+FBA+Spartanburg+SC"},
    {"name": "XB Fulfillment - Reno, NV",
     "url": "https://www.google.com/maps/search/XB+Fulfillment+Reno+NV"},
    {"name": "eFulfillment Service - Traverse City, MI",
     "url": "https://www.google.com/maps/search/eFulfillment+Service+Traverse+City+MI"},
]
