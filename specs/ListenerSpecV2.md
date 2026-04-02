# Signal Listener V2 — Complete Build Spec

## What This Is

An always-on AI-powered competitive intelligence system for **Saltbox** (co-warehousing + fulfillment + community for ecommerce brands). The listener monitors 12 data sources for signals that indicate potential Saltbox customers — people unhappy with their current 3PL/fulfillment solution, actively searching for alternatives, or showing behavioral indicators that they're at the "Saltbox stage" of their business.

This is being built as part of a growth marketing assessment for the Director of Growth role at Saltbox. The v0.1 prototype (Shopify App Store reviews only) is live at: https://natwar86.github.io/reviewtracker/

The full assessment is at: https://natwar86.github.io/saltbox/

## Why This Matters

- Saltbox has 12 physical locations across the U.S. serving 1,000+ ecommerce brands
- Their competitors include ShipBob, ShipMonk, ShipHero (3PLs) and ReadySpaces, Cubework, WareSpace, Portal (co-warehousing)
- Every negative competitor review, every Reddit thread asking "which 3PL should I use?", every DTC brand hiring a warehouse coordinator is a potential Saltbox customer
- Currently, none of this signal data is being captured or acted on

## Saltbox Context (for the AI building this)

- **Saltbox** = co-warehousing + integrated fulfillment services + founder community
- **Parsel** = Saltbox's free shipping platform (competitor to ShipStation, Shippo, Pirate Ship)
- **eForce** = Saltbox's on-demand labor service ($45-60/hr), does FBA prep among other things
- **Target customer** = founder-led DTC ecommerce brand, outgrowing home fulfillment
- **12 locations** in: Atlanta, Dallas, Denver, Houston, Austin, Nashville, Charlotte, Chicago, Salt Lake City, Phoenix, San Antonio, and more
- **Key competitors to monitor**:
  - 3PLs: ShipBob, ShipMonk, ShipHero, Flexport, Flowspace
  - Co-warehousing: ReadySpaces, Cubework, WareSpace, Portal Warehousing, Loloft, FlexEtc
  - Shipping tools (Parsel competitors): ShipStation, Shippo, Pirate Ship, Easyship, Veeqo

---

## PART 1: EXISTING V0.1 PROTOTYPE — WHAT'S ALREADY BUILT

Everything below describes the working system that is already deployed and functional. A new Claude Code session should read this section to understand what exists before building on top of it.

### Live URLs

- **Dashboard**: https://natwar86.github.io/reviewtracker/
- **Repository**: https://github.com/natwar86/reviewtracker.git
- **Parent site**: https://natwar86.github.io/saltbox/

### Repository Structure

```
saltbox-2/
├── scraper.py              # Main scraper script (~807 lines)
├── requirements.txt        # Python dependencies
├── .gitignore              # Excludes venv, debug HTML, progress files, caches
├── PROJECT.md              # Standalone project documentation
├── signal-listener-spec.md # Original v1 spec (kept for reference)
├── ListenerSpecV2.md       # This file
├── docs/                   # GitHub Pages root (deployed from /docs on main branch)
│   ├── index.html          # Single-page dashboard (all CSS/JS inline, no build step)
│   └── data/               # JSON data files loaded by the dashboard
│       ├── shipbob.json    # 307 reviews
│       ├── shiphero.json   # 158 reviews
│       └── shipmonk.json   # 143 reviews
└── output/                 # Raw scraper output (per-app directories)
    ├── shipbob/
    │   ├── reviews.json        # Final deduplicated reviews (same as docs/data/shipbob.json)
    │   ├── reviews.csv         # CSV export
    │   ├── reviews.jsonl       # Raw append-only scrape log (gitignored)
    │   ├── progress.json       # Resume checkpoint (gitignored)
    │   └── store_url_cache.json # URL resolution cache (gitignored)
    ├── shiphero/               # Same structure as shipbob/
    ├── shipmonk/               # Same structure as shipbob/
    └── omnisend/               # Test app used during development (not in dashboard)
```

### What's in git vs gitignored

**Tracked (committed):**
- `scraper.py`, `requirements.txt`, `.gitignore`
- `docs/index.html` and `docs/data/*.json`
- `output/*/reviews.json` and `output/*/reviews.csv`

**Gitignored:**
- `.venv/` — Python virtual environment
- `output/*/debug_html/` — Raw HTML pages saved with `--save-html`
- `output/*/progress.json` — Scraping resume checkpoints
- `output/*/reviews.jsonl` — Raw append-only scrape output
- `output/*/store_url_cache.json` — Store URL resolution cache
- `__pycache__/`, `*.pyc`

### The Scraper (`scraper.py`)

#### Overview

A polite, resumable Shopify App Store review scraper. There is **no official API** for Shopify App Store reviews — scraping server-rendered HTML is the only option.

Reviews are at: `https://apps.shopify.com/{slug}/reviews?page={N}` (~10 reviews per page).

#### Key Design Decisions

1. **Politeness first**: Random delays (configurable, default 4-8s), exponential backoff on errors (30s up to 5min cap), respects `Retry-After` headers. All 3 apps were scraped with `--min-delay 10 --max-delay 20` (slow mode) with zero rate limit errors.

2. **Resumable**: Progress saved to `progress.json` after every page. If interrupted (Ctrl+C, crash, etc.), re-running the same command picks up exactly where it left off.

3. **Store URL resolution**: Reviewer display names are probed as `{slug}.myshopify.com` subdomains using HEAD requests. If the subdomain redirects to a custom domain, that's captured as the store URL. Hit rate: ~38% across 601 unique reviewers (232 resolved).

4. **Deduplication**: Reviews are appended to a JSONL file during scraping, then deduplicated by body text (first 100 chars) when compiling final output.

5. **Sort order**: Final output is sorted by rating ascending (1-star first), then by date. This prioritizes low ratings for competitive intelligence.

#### Python Dependencies

```
requests>=2.31.0        # HTTP client
beautifulsoup4>=4.12.0  # HTML parsing
lxml>=5.0.0             # Fast HTML parser backend
```

Install: `python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`

#### CLI Usage

```bash
# Scrape a single app
python scraper.py shipbob

# Scrape multiple apps sequentially
python scraper.py shipbob shiphero shipmonk

# Read app slugs from a file (one per line, supports full URLs)
python scraper.py --from-file apps.txt

# Limit pages (for testing)
python scraper.py omnisend --max-pages 5

# Slow mode (recommended for production scraping)
python scraper.py shipbob --min-delay 10 --max-delay 20

# Save raw HTML for debugging CSS selectors
python scraper.py omnisend --max-pages 2 --save-html

# Skip store URL resolution (just scrape reviews)
python scraper.py shipbob --skip-resolve

# Verbose logging
python scraper.py shipbob --verbose
```

#### Scraper Architecture

```
main()
 └─ For each app slug:
     └─ scrape_app_reviews(app_slug, fetcher)
         ├─ Load progress.json (resume if exists)
         ├─ Fetch page 1, determine total_pages from pagination
         ├─ For each page (start_page..total_pages):
         │   ├─ PoliteFetcher.fetch(url)  — delays, retries, backoff
         │   ├─ parse_reviews_page(html)  — extract review data
         │   ├─ Append to reviews.jsonl
         │   └─ Save progress.json
         ├─ compile_final_output()  — deduplicate, sort, write JSON+CSV
         └─ resolve_store_urls_for_app()  — probe myshopify.com subdomains
```

#### Key Classes and Functions

**`PoliteFetcher(min_delay, max_delay)`** — HTTP client wrapper
- `fetch(url)` — GET with random delay, up to 5 retries, exponential backoff
- Respects 429/503 status codes and `Retry-After` headers
- Uses `requests.Session` for connection pooling
- Config constants: `INITIAL_BACKOFF=30.0`, `MAX_BACKOFF=300.0`, `BACKOFF_MULTIPLIER=2.0`, `MAX_RETRIES=5`

**`parse_reviews_page(html)`** — HTML parser using these exact CSS selectors:
```python
# Review blocks
review_blocks = soup.select("[data-merchant-review]")

# Per block:
review_id    = block.get("data-review-content-id", "")
rating       = block.select_one('div[role="img"][aria-label*="star"]')  # parses "N out of 5 stars"
date         = block.select_one(".tw-text-body-xs.tw-text-fg-tertiary")  # strips "Edited " prefix
body         = block.select_one("[data-truncate-review]:not([data-reply-id]) [data-truncate-content-copy]")
reviewer     = block.select_one(".tw-text-heading-xs span[title]")  # title attribute has full name
info_parent  = block.select_one("[class*='tw-order-1'][class*='tw-row-span']")  # location + usage duration
review_link  = block.select_one("[data-review-share-link]")  # data-review-share-link attribute
```

**IMPORTANT**: These selectors are specific to Shopify's HTML as of March 2026. If Shopify redesigns their review pages, these will break. Use `--save-html` to debug and inspect `output/{app}/debug_html/`.

**`slugify_name(name)`** — Generates myshopify subdomain candidates:
- `"Make Believe Co."` → `["make-believe-co", "make-believe", "makebelieveco"]`
- Strips unicode, common business suffixes (inc, llc, co, corp, company, store, shop)
- Tries hyphenated, non-hyphenated, and "the-" stripped variants

**`resolve_store_url(reviewer_name, fetcher)`** — HEAD request to `{slug}.myshopify.com`:
- Follows redirects; checks `resp.history` for first redirect leaving myshopify.com
- Strips `/password` suffix (stores behind password pages)
- Upgrades `http://` to `https://`

**`compile_final_output(app_slug)`** — Post-processing:
- Reads JSONL, deduplicates by first 100 chars of body
- Sorts: rating ascending, then date ascending
- Writes `reviews.json` (array) and `reviews.csv`

**`resolve_store_urls_for_app(app_slug, fetcher)`** — Batch URL resolution:
- Loads `store_url_cache.json` to skip already-resolved names
- Resolves all unique reviewer names not yet in cache
- Saves cache every 10 resolutions
- Rewrites `reviews.json` and `reviews.csv` with `store_url` field populated

#### Review Data Schema

Each review object in the JSON files:

```json
{
  "review_id": "1718756",
  "rating": 1.0,
  "date": "April 23, 2025",
  "body": "It's been a nightmare. Their fullfilment is almost double of what you see on shopify...",
  "reviewer": "YOUCANIC",
  "location": "United States",
  "usage_duration": "7 months using the app",
  "review_link": "/reviews/1718756",
  "app_slug": "shipbob",
  "app_url": "https://apps.shopify.com/shipbob",
  "store_url": "https://shop.youcanic.com"
}
```

| Field | Type | Notes |
|-------|------|-------|
| `review_id` | string | Shopify's internal ID from `data-review-content-id` |
| `rating` | float | 1.0 to 5.0 |
| `date` | string | Human-readable, e.g. "April 23, 2025". "Edited" prefix stripped. |
| `body` | string | Full review text. Paragraphs joined with `\n`. |
| `reviewer` | string | Display name (business/store name). Not a person's name. |
| `location` | string | Country, e.g. "United States", "United Kingdom" |
| `usage_duration` | string | e.g. "7 months using the app", "Over 2 years using the app" |
| `review_link` | string | Relative path, e.g. "/reviews/1718756" |
| `app_slug` | string | The app identifier, e.g. "shipbob" |
| `app_url` | string | Full URL, e.g. "https://apps.shopify.com/shipbob" |
| `store_url` | string | Resolved store URL, or empty string if not found |

#### Current Data (as of March 8, 2026)

| App | Reviews | Store URLs Resolved | Resolution Rate |
|-----|---------|--------------------|----|
| ShipBob | 307 | 112 | 36.5% |
| ShipHero | 158 | 76 | 48.1% |
| ShipMonk | 143 | 44 | 30.8% |
| **Total** | **608** | **232** | **38.2%** |

There is also an `output/omnisend/` directory with 18 test reviews from initial development. This is NOT included in the dashboard.

### The Dashboard (`docs/index.html`)

#### Overview

A single-page HTML file with all CSS and JS inline — no build step, no framework. Hosted on GitHub Pages from the `/docs` directory on `main` branch.

#### Design System

The dashboard matches the Saltbox Growth Engine site (`https://natwar86.github.io/saltbox/`):

**Colors** (CSS variables in `:root`):
- `--paper: #f5f0e8` — Warm beige background
- `--ink: #1a1a1a` — Near-black text
- `--accent: #c2491d` — Rust/terracotta (primary accent, star fill color)
- `--card-bg: #fffdf8` — Off-white card backgrounds
- `--tag-bg: #f0e8d8` — Table header background
- `--border: #d4cec2` — Soft taupe borders
- `--muted: #8a8275` — Secondary text
- `--gap-red: #b83a2a` — Low rating indicator (1-3 star row highlight)
- `--opp-green: #2a7d4f` — ShipHero badge color
- `--strategic-blue: #2a5a8a` — ShipBob badge color, store link color

**Typography** (Google Fonts, loaded via `<link>`):
- `--serif: 'DM Serif Display'` — Headlines, stat values
- `--sans: 'DM Sans'` — Body text, inputs
- `--mono: 'DM Mono'` — Labels, data cells, monospace elements

**Visual details**:
- SVG fractal noise grain overlay (0.04 opacity, fixed position, z-index 9999)
- `fadeUp` entrance animations (staggered 0.1s-0.4s for page sections, 0.015s per table row)
- Card-based layout with 8px border radius, 1px solid borders
- 80% max-width content area (100% on mobile < 768px)
- No top navigation bar (removed — the nav links from the parent Saltbox site don't apply here)

#### Dashboard Features

1. **Data loading**: Fetches `data/{slug}.json` for each app via `fetch()` + `Promise.allSettled()`. The `DATA_FILES` array on line 674 controls which apps are loaded:
   ```js
   const DATA_FILES = ['shipbob', 'shiphero', 'shipmonk'];
   ```

2. **Stats strip**: 5 stat cards — Total reviews, Avg rating, Apps tracked, Stores resolved, Low ratings (1-3)

3. **Filters**:
   - App dropdown (populated dynamically from loaded data)
   - Star rating buttons (1-5 + All) — filters to exact rating match
   - Location text search (substring match, case-insensitive, 250ms debounce)
   - Keyword search across review body (substring match, case-insensitive, 250ms debounce)

4. **Sorting**: Click any column header (App, Rating, Reviewer, Location, Date, Usage). Default: rating ascending (1-star first). Toggles asc/desc on re-click. Visual indicator: accent-colored column header + arrow.

5. **Low rating highlight**: Rows with rating 1-3 get a `--gap-red` left border and tinted background

6. **Expandable review text**: Truncated to 2 CSS lines by default (`-webkit-line-clamp: 2`), click text or "expand" button to show full review

7. **Pagination**: 50 reviews per page (`PER_PAGE = 50`) with numbered page buttons and prev/next arrows

8. **App badges**: Color-coded per app — ShipBob=blue (`--strategic-blue`), ShipHero=green (`--opp-green`), ShipMonk=rust (`--accent`)

9. **Store links**: Clickable links to resolved store URLs, displayed as clean domain names (protocol and www stripped). Shows em-dash for unresolved stores.

10. **Footer**: Matches parent Saltbox site — Natwar Maheshwari, contact info, "Built with AI as a force multiplier"

#### Adding a New App to the Dashboard

1. Run the scraper: `python scraper.py new-app-slug --min-delay 10 --max-delay 20`
2. Copy the output: `cp output/new-app-slug/reviews.json docs/data/new-app-slug.json`
3. Edit `docs/index.html` line 674: add the slug to the `DATA_FILES` array:
   ```js
   const DATA_FILES = ['shipbob', 'shiphero', 'shipmonk', 'new-app-slug'];
   ```
4. Add a CSS badge color for `.app-new-app-slug` in the `<style>` block (around line 331)
5. Commit and push — GitHub Pages will auto-deploy

### Deployment

- **Hosting**: GitHub Pages, served from `/docs` directory on `main` branch
- **Repository**: https://github.com/natwar86/reviewtracker.git
- **Branch**: `main` (only branch)
- **No build step**: Push to `main` and GitHub Pages serves `docs/index.html` directly
- **Deploy time**: Typically 1-2 minutes after push

### Known Limitations of V0.1

1. **Shopify HTML structure may change**: The scraper relies on specific CSS selectors (`[data-merchant-review]`, `.tw-text-heading-xs`, etc.) that could break if Shopify redesigns their review pages. If parsing breaks, use `--save-html` and inspect the HTML in `output/{app}/debug_html/`.

2. **Store URL resolution is imperfect**: Only ~38% of reviewers have resolvable myshopify.com subdomains. Many stores use names that don't match their subdomain, or have closed.

3. **Dates are human-readable strings**: The `date` field is stored as-is from Shopify (e.g. "April 23, 2025"), not ISO format. The dashboard's JS `parseDate()` handles this via `new Date(dateStr)`.

4. **No automatic re-scraping**: The data is static. To update, re-run the scraper manually. Delete `output/{app}/progress.json` to force a full re-scrape (otherwise it resumes from where it left off).

5. **Rating is float**: Stored as `1.0` not `1`. The dashboard compares with `===` against integers from the filter buttons, which works because JS `1.0 === 1` is `true`.

6. **No AI classification**: Reviews are shown as-is. No sentiment analysis, pain point extraction, or urgency scoring. This is the primary addition for V2.

7. **No cross-source data**: Only Shopify App Store reviews. No Reddit, Google Maps, Twitter, etc.

8. **No alerts**: No notifications when new high-signal reviews appear.

---

## PART 2: V2 ARCHITECTURE — THE FULL SIGNAL LISTENER

This section describes what needs to be built on top of the existing V0.1 prototype.

### Architecture Overview

```
┌─────────────────────────────────────────────────┐
│                 DATA COLLECTORS                   │
│  (one per source, runs on schedule or webhook)    │
├───────────┬───────────┬───────────┬──────────────┤
│ Shopify   │ Reddit    │ Google    │ Job Boards   │
│ Reviews   │ API       │ Maps API  │ Scraper      │
├───────────┼───────────┼───────────┼──────────────┤
│ Crunchbase│ Twitter/X │ Amazon    │ Trustpilot   │
│ API       │ API       │ Forums    │ /BBB         │
├───────────┼───────────┼───────────┼──────────────┤
│ LinkedIn  │ Macro     │ Shopify   │ YouTube      │
│ (manual)  │ Triggers  │ Community │ Data API     │
└─────┬─────┴─────┬─────┴─────┬─────┴──────┬───────┘
      │           │           │            │
      ▼           ▼           ▼            ▼
┌─────────────────────────────────────────────────┐
│              SIGNAL PROCESSOR                     │
│  - Normalize to common schema                     │
│  - AI sentiment classification                    │
│  - Pain point extraction                          │
│  - Urgency scoring (hot/warm/cold)                │
│  - Market matching (which of 12 cities)           │
│  - Deduplication across sources                   │
└──────────────────────┬──────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────┐
│                 DASHBOARD                         │
│  - Signal feed (filterable by source/urgency/     │
│    market/pain type)                              │
│  - Hot signal alerts                              │
│  - Trend analytics                                │
│  - Content brief generator                        │
└─────────────────────────────────────────────────┘
```

### Common Signal Schema

Every signal from every source should normalize to this structure. This replaces the V0.1 review-only schema — existing Shopify review data should be migrated into this format.

```json
{
  "id": "uuid",
  "source": "shopify_reviews | reddit | google_maps | job_postings | crunchbase | twitter | amazon_forums | trustpilot | bbb | linkedin | macro | shopify_community | youtube",
  "source_url": "https://...",
  "timestamp": "2026-03-08T12:00:00Z",
  "collected_at": "2026-03-08T12:05:00Z",

  "author": {
    "name": "string or null",
    "profile_url": "string or null",
    "company": "string or null (extracted by AI if possible)",
    "company_url": "string or null"
  },

  "content": {
    "title": "string or null",
    "body": "full text of review/post/comment/listing",
    "rating": "number or null (1-5 for reviews)"
  },

  "classification": {
    "sentiment": "negative | neutral | positive",
    "urgency": "hot | warm | cold",
    "pain_type": ["cost", "control", "service_quality", "scale", "speed", "customization"],
    "competitor_mentioned": ["ShipBob", "ShipMonk", "etc."],
    "market": "atlanta | dallas | denver | ... | unknown",
    "intent": "switching | exploring | complaining | asking | hiring | scaling",
    "summary": "AI-generated one-line summary of the signal"
  }
}
```

#### Mapping V0.1 Review Data → V2 Signal Schema

For the existing 608 Shopify reviews, the migration would be:

| V0.1 Field | V2 Field | Notes |
|------------|----------|-------|
| `review_id` | `id` | Prefix with `shopify_` for namespacing |
| — | `source` | Always `"shopify_reviews"` |
| `review_link` | `source_url` | Prepend `https://apps.shopify.com` |
| `date` | `timestamp` | Parse "April 23, 2025" → ISO 8601 |
| — | `collected_at` | Set to migration time |
| `reviewer` | `author.name` | |
| — | `author.profile_url` | Not available from Shopify |
| `reviewer` | `author.company` | Same as name (reviewer names are store/business names) |
| `store_url` | `author.company_url` | Already resolved |
| — | `content.title` | Not available (Shopify reviews have no title) |
| `body` | `content.body` | |
| `rating` | `content.rating` | |
| — | `classification.*` | **Needs AI classification** — this is new work |

Additional V0.1 fields (`app_slug`, `app_url`, `location`, `usage_duration`) should be preserved as extra metadata, either in a `metadata` object or as top-level fields alongside the common schema.

### Signal Sources — Detailed Specs

#### Source 1: Shopify App Store Reviews

**Status**: BUILT in V0.1. Needs extension for V2.

**What already works** (see Part 1 for full details):
- Scraper (`scraper.py`) fetches all reviews for any app slug
- Polite fetching with delays, retries, exponential backoff
- HTML parsing with precise CSS selectors
- Store URL resolution via myshopify.com subdomain probing
- Resumable progress tracking
- Dashboard display with filters and sorting

**Apps currently scraped**: ShipBob (307), ShipHero (158), ShipMonk (143)

**Apps to ADD for V2**:
| App | Shopify URL | Notes |
|-----|-------------|-------|
| Fulfillrite | apps.shopify.com/fulfillrite | Competitor 3PL |
| Parsel (own) | apps.shopify.com/saltbox | Saltbox's own app — monitor sentiment |

**What needs to change for V2**:
1. **Scheduled re-scraping**: Currently manual. Need to run on a schedule (daily) and detect only NEW reviews (diff against last run).
2. **AI classification**: Run each review through the classification prompt to populate `classification.*` fields.
3. **Schema migration**: Convert existing review data to the common signal schema.
4. **Incremental updates**: Don't re-scrape everything — detect `last_completed_page` and check for new pages. Also detect new reviews on existing pages (Shopify adds new reviews to page 1).

---

#### Source 2: Reddit

**Subreddits to monitor**:
- r/ecommerce (~350K members)
- r/FulfillmentByAmazon (~150K)
- r/smallbusiness (~1.5M)
- r/Entrepreneur (~3.5M)
- r/shopify (~180K)
- r/ecommerceseller
- r/AmazonSeller

**Keywords to match** (title or body):
- Direct: "warehouse space", "co-warehousing", "shared warehouse", "ecommerce warehouse"
- Competitor: "ShipBob", "ShipMonk", "ShipHero", "Flexport", "3PL"
- Intent: "leaving my 3PL", "unhappy with fulfillment", "need warehouse", "outgrowing", "scaling fulfillment"
- FBA: "FBA prep", "prep center", "FBA alternative"
- General: "fulfillment options", "which 3PL", "warehouse recommendation", "self-fulfillment vs 3PL"

**Collection method**:
- Reddit API (OAuth2, free tier: 100 requests/minute)
- Use `/r/{subreddit}/search.json?q={keyword}&sort=new&limit=25` for keyword searches
- Or use `/r/{subreddit}/new.json` and filter locally
- Also monitor specific competitor subreddits if they exist
- Schedule: every 30 minutes for high-priority subs, hourly for others

**Important**: Reddit content is the #1 cited domain in LLM responses (40.1% of citations). Responding helpfully in these threads has triple value: direct lead gen, Reddit SEO, and influencing what AI systems recommend.

---

#### Source 3: Google Maps Reviews

**Competitors to monitor** (by location):

Need to find Google Place IDs for each competitor location in Saltbox's 12 metro markets:
- ReadySpaces locations
- Cubework locations
- WareSpace locations
- Portal Warehousing locations
- Loloft locations (LA, Dallas, NYC)
- FlexEtc locations (TX markets)

Also monitor Saltbox's own 12 locations for sentiment tracking.

**Collection method**:
- Google Places API — `place/details` endpoint with `reviews` field
- Requires Google Cloud project + API key
- Free tier: limited, then $17 per 1,000 requests
- Extract: reviewer name, rating, text, time, reply status
- Schedule: daily for competitor locations, real-time monitoring for own locations
- Alternative: Google My Business API for Saltbox's own locations (requires business verification)

**AI classification focus**:
- Negative competitor reviews mentioning "switching", "looking for alternatives", "terrible experience"
- Location-specific signals (a bad review on a ReadySpaces in Atlanta = opportunity for Saltbox Atlanta)

---

#### Source 4: Job Postings

**What signals "Saltbox stage"**:
A small ecommerce brand (< 50 employees) hiring for warehouse/fulfillment roles. They might need a warehouse, not just a person.

**Job titles to monitor**:
- "Warehouse Coordinator"
- "Fulfillment Manager"
- "Shipping Associate"
- "Warehouse Manager"
- "Logistics Coordinator"
- "Inventory Manager"
- "FBA Prep Associate"
- "Ecommerce Operations Manager"

**Filters**:
- Company size: < 50 employees
- Industry: ecommerce, DTC, retail, consumer goods, CPG
- Location: Saltbox's 12 metro markets
- Exclude: large retailers, Amazon itself, established 3PLs

**Collection method**:
- **Option A**: LinkedIn Jobs API (via LinkedIn Marketing API — requires partnership/app approval)
- **Option B**: Indeed Publisher API or scrape Indeed job listings
- **Option C**: Greenhouse/Lever board scraping (many DTC brands use these)
- **Option D**: Google Jobs API (aggregates from multiple boards)
- **Recommended**: Start with Indeed or Google Jobs, easiest access
- Schedule: daily

**AI classification focus**:
- Company research: look up the company, determine if they're ecommerce/DTC
- Size estimation: LinkedIn employee count or job description clues
- Location matching to Saltbox markets

---

#### Source 5: Funding Announcements

**What to listen for**:
DTC/ecommerce brands raising seed or Series A ($500K - $5M). Post-funding, they need to scale operations fast.

**Collection method**:
- **Crunchbase API** (Basic: $29/mo, Pro: $49/mo)
  - Filter: `categories=ecommerce,direct-to-consumer,consumer-goods,cpg`
  - Filter: `funding_type=seed,series_a`
  - Filter: `announced_on >= {last_check_date}`
  - Filter: `money_raised >= 500000 AND money_raised <= 5000000`
- **Alternative**: TechCrunch RSS feed + PitchBook + press release monitoring
- Schedule: daily

**AI classification focus**:
- Is this a product-based brand that ships physical goods? (not SaaS, not services)
- Location: are they near a Saltbox market?
- Stage: are they at the "outgrowing home" inflection point?

---

#### Source 6: Twitter/X

**Accounts to monitor for mentions/complaints**:
- @ShipBob
- @ShipMonk
- @ShipHero
- @Flexport

**Keywords**:
- "3PL problems"
- "fulfillment nightmare"
- "shipping disaster"
- "warehouse space"
- "leaving ShipBob" / "left ShipBob" (and for each competitor)
- "ecommerce fulfillment"

**Collection method**:
- X API v2 — Filtered Stream endpoint
- Set up stream rules: `(@ShipBob OR @ShipMonk) -is:retweet` for competitor mentions
- Keyword rules: `"3PL problems" OR "fulfillment nightmare" OR "leaving ShipBob"`
- Basic tier: $100/mo, 10,000 tweets/month read
- Pro tier: $5,000/mo — likely overkill, Basic should suffice
- Alternative: Free tier with search endpoint (polling every 15 min, 500K tweets/month read)
- Schedule: real-time via stream, or polling every 15 minutes

**AI classification focus**:
- Separate actual complaints from jokes/memes
- Extract company info from profile bio
- Low volume but high intent — people tweeting complaints are actively frustrated

---

#### Source 7: Amazon Seller Forums

**Why this matters**: Amazon killed in-house FBA prep on Jan 1, 2026. Sellers are scrambling for alternatives. Saltbox's eForce does all 7 required FBA prep activities.

**Forums to monitor**:
- Seller Central Forums: `sellercentral.amazon.com/forums`
- Key categories: "Fulfillment by Amazon", "Shipping & Delivery", "Account Health"

**Keywords**:
- "FBA prep"
- "prep center"
- "FBA prep alternative"
- "prep service"
- "warehouse for FBA"
- "self-fulfillment"
- "leaving FBA"

**Collection method**:
- No official API. Web scraping required.
- Use Puppeteer/Playwright for JS-rendered pages
- Or use a service like ScrapingBee/Bright Data
- Monitor new posts and filter by keywords
- Schedule: every 2-4 hours (forums move slower than Reddit)

**AI classification focus**:
- Is the seller in a Saltbox market?
- Are they looking for a prep center or doing it themselves?
- Volume indicators: how many units are they shipping?

---

#### Source 8: Trustpilot / BBB

**Companies to monitor on Trustpilot**:
- ShipBob: trustpilot.com/review/shipbob.com
- ShipMonk: trustpilot.com/review/shipmonk.com
- Flexport: trustpilot.com/review/flexport.com
- ReadySpaces: trustpilot.com/review/readyspaces.com (if listed)

**BBB**:
- Search for competitor business profiles
- BBB complaints are public

**Collection method**:
- **Trustpilot**: Business API (requires business account) or scrape review pages
  - Reviews are paginated, sortable by date
  - Extract: rating, title, body, date, author
- **BBB**: Scrape complaint pages (public data)
- Schedule: daily
- Cross-reference reviewers across Shopify + Trustpilot + BBB to identify merchants who are vocal on multiple platforms

---

#### Source 9: LinkedIn Posts

**What to listen for**:
Founders posting about scaling pain, fulfillment challenges, warehouse needs.

**Keywords/phrases**:
- "outgrew our garage"
- "drowning in orders"
- "need warehouse space"
- "fulfillment challenges"
- "scaling operations"
- "3PL frustration"
- "shipping costs"

**Collection method**:
- LinkedIn doesn't allow automated scraping (ToS violation)
- **Recommended approach**: LinkedIn Sales Navigator ($99/mo)
  - Create saved searches with keyword + company size + industry filters
  - Manual review with a daily 15-minute cadence
  - Flag posts for Tyler Scriven (CEO) to engage founder-to-founder
- **Alternative**: Use a tool like Phantombuster's LinkedIn post monitor (gray area, use carefully)
- This source is best as semi-automated: AI-assisted discovery, human engagement

**Note**: This is the one source where the response should come from a real person (Tyler or a team member), not automated. Founder-to-founder engagement on LinkedIn is the highest-trust channel.

---

#### Source 10: Macro Triggers

**Sources to monitor**:
- Modern Retail (modernretail.co)
- Practical Ecommerce (practicalecommerce.com)
- Supply Chain Dive (supplychaindive.com)
- Freight Waves (freightwaves.com)
- Amazon Seller News / Amazon Announcements
- Shopify Changelog / Shopify Blog
- USPS/UPS/FedEx rate announcements

**Trigger types**:
- Tariff changes affecting imported goods
- Amazon policy changes (like the FBA prep change)
- Shopify platform changes
- Carrier rate increases
- New competitor launches or shutdowns
- Industry consolidation (M&A news)

**Collection method**:
- Google Alerts for key terms: "ecommerce fulfillment", "3PL", "warehouse space", "FBA changes", "shipping rates 2026"
- RSS feeds from industry publications
- Schedule: real-time (Google Alerts are push-based), RSS check every hour

**AI classification focus**:
- Does this trigger create urgency for ecommerce operators?
- What's the content angle? (blog post, ad campaign, outreach message)
- Which Saltbox service does this relate to? (warehouse, Parsel, eForce)

---

#### Source 11: Shopify Community Forums

**URL**: community.shopify.com

**Categories to monitor**:
- Shopify Discussions > Store Feedback
- Shopify Discussions > Ecommerce Marketing
- Technical Q&A (for shipping/fulfillment questions)

**Keywords**:
- "fulfillment"
- "warehouse"
- "shipping solution"
- "3PL"
- "shipping app"
- "Parsel" (monitor own brand mentions)

**Collection method**:
- Shopify Community has RSS feeds for categories
- Parse RSS for new posts matching keywords
- Alternative: web scraping with keyword filter
- Schedule: every 2 hours

**AI classification focus**:
- Is this merchant asking about fulfillment? Shipping tools? Warehouse space?
- Are they a good fit for Parsel (shipping) or Saltbox membership (warehouse)?

---

#### Source 12: YouTube Comments

**Videos to monitor**:
- Search YouTube for: "ShipBob review", "3PL review", "ecommerce fulfillment", "warehouse for ecommerce", "FBA prep"
- Monitor the top 20-30 videos by view count in these categories
- Also monitor new videos as they're published

**What to listen for in comments**:
- "Any alternatives to ShipBob?"
- "I left ShipBob because..."
- "Looking for a warehouse in [city]"
- "Is there a cheaper option?"
- General complaints about 3PL service

**Collection method**:
- YouTube Data API v3
  - `search` endpoint to find relevant videos
  - `commentThreads` endpoint to get comments per video
  - Free quota: 10,000 units/day (each commentThreads request = ~3 units)
- Store video IDs of high-traffic relevant videos, poll for new comments
- Schedule: daily for known videos, weekly discovery of new videos

**AI classification focus**:
- Filter out spam/bots (YouTube comments are noisy)
- Extract actual pain points from genuine merchants
- Low competition — almost no brands engage in YouTube comments

---

## PART 3: AI CLASSIFICATION LAYER

Every signal runs through an AI classification step before entering the dashboard.

**Model recommendation**: Claude Haiku (fast, cheap) or GPT-4o-mini for classification. Full models (Opus, GPT-4) only for generating response drafts.

**Classification prompt template**:
```
You are classifying a signal for Saltbox, a co-warehousing company for ecommerce brands.

Signal source: {source}
Content: {content}
Rating (if applicable): {rating}

Classify this signal:
1. Sentiment: negative / neutral / positive
2. Urgency: hot (actively looking to switch/buy) / warm (frustrated but not yet switching) / cold (general discussion)
3. Pain type (select all that apply): cost, control, service_quality, scale, speed, customization
4. Competitors mentioned: list any 3PL or warehouse companies mentioned
5. Market: if a city/state is mentioned, which of Saltbox's markets is closest? (atlanta, dallas, denver, houston, austin, nashville, charlotte, chicago, salt_lake_city, phoenix, san_antonio, unknown)
6. Intent: switching / exploring / complaining / asking / hiring / scaling
7. Summary: one sentence summarizing what this person needs

Return as JSON.
```

---

## PART 4: DASHBOARD REQUIREMENTS FOR V2

The V2 dashboard replaces the current review-only table view with a multi-source signal feed. It should maintain the same Saltbox design system (see Part 1 for exact CSS variables, fonts, and visual patterns).

### Signal Feed
- Reverse chronological list of all signals
- Filterable by: source, urgency, market, pain type, competitor, date range
- Each signal card shows: source icon, timestamp, author (if known), company (if known), AI summary, urgency badge, pain type tags
- Click to expand for full content + source link

### Hot Signal Alerts
- Any signal classified as "hot" triggers:
  - Dashboard notification (visual + sound)
  - Email alert to configured recipients
  - Optional: Slack webhook
- Include suggested response draft (AI-generated)

### Trend Analytics
- Signals per day/week by source
- Most common pain types over time
- Competitor mention frequency
- Market distribution (which cities are generating the most signals)
- Emerging keywords/topics

### Content Brief Generator
- Aggregate the most common complaints/questions from the past 30 days
- Auto-generate content briefs: "20 people asked about FBA prep alternatives on Reddit this month → blog post brief"
- Suggest ad copy angles based on pain points

---

## PART 5: TECH STACK SUGGESTIONS

- **Frontend**: React or Next.js (dashboard) — or keep it simple with a single HTML file like the V0.1 prototype
- **Backend**: Node.js or Python (data collection scripts)
- **Database**: Supabase (free tier) or SQLite for prototype
- **Scheduling**: cron jobs, or Vercel cron, or GitHub Actions on schedule
- **AI**: Anthropic API (Claude Haiku for classification) or OpenAI API (GPT-4o-mini)
- **Hosting**: GitHub Pages for frontend, Vercel/Railway for backend scripts
- **Alerts**: Email via Resend/SendGrid, Slack webhooks

---

## PART 6: BUILD PRIORITY

### Phase 1 (v0.2 — extend existing prototype)
1. Add AI sentiment classification to existing Shopify review data (608 reviews)
2. Extend Shopify review scraper to detect new reviews (incremental scraping)
3. Add Reddit API integration (highest signal-to-noise ratio after Shopify)
4. Update dashboard to show classification data (urgency badges, pain type tags, sentiment)
5. Add Fulfillrite and Parsel (Saltbox's own app) to Shopify scraping

### Phase 2
6. Google Maps API integration
7. Twitter/X API integration
8. Trustpilot scraper
9. Hot signal alerts (email/Slack)

### Phase 3
10. Job posting monitoring
11. Crunchbase funding announcements
12. YouTube Data API
13. Amazon Seller Forums scraper
14. Shopify Community monitoring
15. Macro triggers (RSS + Google Alerts)

### Phase 4
16. Trend analytics
17. Content brief generator
18. Cross-source deduplication (same reviewer on Shopify + Trustpilot)
19. LinkedIn integration (semi-automated)

---

## Key Design Principle

This is a **growth marketing tool**, not an enterprise monitoring platform. Keep it scrappy and functional. A working prototype that captures 80% of the value is better than a polished system that takes months to build. The goal is to demonstrate that this approach works and generates real pipeline — not to build production infrastructure on day one.
