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
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
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

# Classification goes through OpenRouter so billing isn't tied to one
# provider: primary model first, then automatic failover down the list.
CLASSIFICATION_MODELS = [
    "anthropic/claude-haiku-4.5",
    "google/gemini-2.5-flash",
]

# Major US metros for geo-tagging signals (stored in the signals.market column).
# Buyers of the feed are location-sensitive (co-warehouses operate per-metro).
US_METROS = [
    "atlanta", "dallas", "denver", "houston", "austin",
    "nashville", "charlotte", "chicago", "salt_lake_city",
    "phoenix", "san_antonio", "los_angeles", "new_york",
    "miami", "fort_lauderdale", "seattle", "san_francisco",
    "boston", "philadelphia", "minneapolis", "washington_dc",
    "portland", "las_vegas", "other_us", "non_us",
]

COMPETITORS = {
    "3pl": ["ShipBob", "ShipMonk", "ShipHero", "Flexport", "Flowspace", "Fulfillrite"],
    "cowarehouse": ["ReadySpaces", "Cubework", "WareSpace", "Portal Warehousing", "Loloft", "FlexEtc"],
    "shipping": ["ShipStation", "Shippo", "Pirate Ship", "Easyship", "Veeqo"],
}

# Display names for reviewed Shopify apps (slug -> brand), used to tell the
# classifier which company a review is about.
SHOPIFY_APP_NAMES = {
    "shipbob": "ShipBob",
    "shiphero": "ShipHero",
    "shipmonk": "ShipMonk",
    "fulfillrite-order-fulfillment": "Fulfillrite",
    "shipstation": "ShipStation",
    "easyship": "Easyship",
    "skusavvy": "SKUSavvy",
}

# ---------------------------------------------------------------------------
# G2 + Capterra (Apify zen-studio/software-review-scraper)
# ---------------------------------------------------------------------------

APIFY_SOFTWARE_REVIEWS_ACTOR = "zen-studio/software-review-scraper"

# Pay-per-event (USD), free tier, verified 2026-07-20 via
# api.apify.com/v2/acts/zen-studio~software-review-scraper.
# NOTE: no date filter and maxResults floors at 100/brand, so every run
# re-pays for the newest slice — the pipeline runs this monthly, not per-cron.
APIFY_SR_COST_PER_RUN = 0.05
APIFY_SR_COST_PER_REVIEW = 0.00499

# Brand names are free-text queries the actor resolves to products; the
# collector drops results whose productName doesn't match the brand.
SOFTWARE_REVIEW_BRANDS = [
    "ShipStation", "ShipBob", "ShipHero", "ShipMonk", "Easyship",
]

# ---------------------------------------------------------------------------
# Google Maps (Apify compass/google-maps-reviews-scraper)
# ---------------------------------------------------------------------------

APIFY_REVIEWS_ACTOR = "compass/google-maps-reviews-scraper"

# Pay-per-event pricing (USD) — used for cost estimation guard rails.
# Verified 2026-07-19 via api.apify.com/v2/acts/compass~google-maps-reviews-scraper:
# $0.00005 per actor start (per GB, 1 GB default) + $0.0006/review on the free
# plan; there is NO per-place charge on this actor. (The old constants here
# were from compass/crawler-google-places, a different actor.)
APIFY_COST_PER_RUN = 0.00005
APIFY_COST_PER_PLACE = 0.0
APIFY_COST_PER_REVIEW = 0.0006

# ---------------------------------------------------------------------------
# Trustpilot (Apify automation-lab/trustpilot)
# ---------------------------------------------------------------------------

APIFY_TRUSTPILOT_ACTOR = "automation-lab/trustpilot"

# Pay-per-event (USD), free-plan tier, verified 2026-07-19 via
# api.apify.com/v2/acts/automation-lab~trustpilot
APIFY_TP_COST_PER_RUN = 0.005
APIFY_TP_COST_PER_REVIEW = 0.000575

# Target brands. url must be the exact Trustpilot review-page URL — business
# units are keyed inconsistently (some with www., some without), and the
# actor returns 0 reviews for a slug variant that doesn't exist.
TRUSTPILOT_COMPANIES: list[dict] = [
    # Slugs verified by probe run 2026-07-19; skusavvy.com has no Trustpilot page.
    {"name": "ShipStation", "url": "https://www.trustpilot.com/review/www.shipstation.com"},  # 628 reviews
    {"name": "ShipBob", "url": "https://www.trustpilot.com/review/shipbob.com"},              # 1014
    {"name": "ShipHero", "url": "https://www.trustpilot.com/review/shiphero.com"},            # 611
    {"name": "ShipMonk", "url": "https://www.trustpilot.com/review/shipmonk.com"},            # 418
    {"name": "Easyship", "url": "https://www.trustpilot.com/review/www.easyship.com"},        # 684
    {"name": "Fulfillrite", "url": "https://www.trustpilot.com/review/fulfillrite.com"},      # 153
]

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

    # --- Expansion 2026-07-20: addresses verified via company sites/press
    # releases/LoopNet. WareSpace's two new CA sites are in lease-up and sit
    # inside the Cubework/ReadySpaces angry-reviewer cluster (buyer #3 pitch).
    {"name": "WareSpace - Santa Ana",
     "url": "https://www.google.com/maps/search/WareSpace+2601+S+Garnsey+St+Santa+Ana+CA"},
    {"name": "WareSpace - Santa Fe Springs",
     "url": "https://www.google.com/maps/search/WareSpace+13711+Freeway+Dr+Santa+Fe+Springs+CA"},
    {"name": "WareSpace - Houston",
     "url": "https://www.google.com/maps/search/WareSpace+10795+Hammerly+Blvd+Houston+TX"},
    {"name": "WareSpace - Phoenix",
     "url": "https://www.google.com/maps/search/WareSpace+9801+S+51st+St+Phoenix+AZ"},
    {"name": "Cubework - City of Industry (Turnbull)",
     "url": "https://www.google.com/maps/search/Cubework+900+Turnbull+Canyon+Rd+City+of+Industry+CA"},
    {"name": "Cubework - City of Industry (Stimson)",
     "url": "https://www.google.com/maps/search/Cubework+347+S+Stimson+Ave+City+of+Industry+CA"},
    {"name": "Cubework - City of Industry (Azusa)",
     "url": "https://www.google.com/maps/search/Cubework+929+Azusa+Ave+City+of+Industry+CA"},
    {"name": "Cubework - Ontario Airport",
     "url": "https://www.google.com/maps/search/Cubework+3950+E+Airport+Dr+Ontario+CA"},
    {"name": "Cubework - Ontario (Doubleday)",
     "url": "https://www.google.com/maps/search/Cubework+1001+Doubleday+Ave+Ontario+CA"},
    {"name": "Cubework - Irvine",
     "url": "https://www.google.com/maps/search/Cubework+2323+Main+St+Irvine+CA"},
    {"name": "ReadySpaces - San Jose",
     "url": "https://www.google.com/maps/search/ReadySpaces+205+E+Alma+Ave+San+Jose+CA"},
    {"name": "ReadySpaces - Santa Clara",
     "url": "https://www.google.com/maps/search/ReadySpaces+1185+Campbell+Ave+San+Jose+CA"},
    {"name": "ReadySpaces - Los Angeles (Downtown)",
     "url": "https://www.google.com/maps/search/ReadySpaces+1919+Vineburn+Ave+Los+Angeles+CA"},
    {"name": "ReadySpaces - South Gate",
     "url": "https://www.google.com/maps/search/ReadySpaces+5625+Firestone+Blvd+South+Gate+CA"},
    {"name": "ReadySpaces - Gardena",
     "url": "https://www.google.com/maps/search/ReadySpaces+153+W+Rosecrans+Ave+Gardena+CA"},
    {"name": "ReadySpaces - Northridge",
     "url": "https://www.google.com/maps/search/ReadySpaces+21350+Lassen+St+Chatsworth+CA"},

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
