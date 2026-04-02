# Shopify App Store Review Tracker

## What This Project Does

This project scrapes all reviews from the Shopify App Store for specific fulfillment apps (ShipBob, ShipHero, ShipMonk), resolves reviewer names to actual Shopify store URLs, and presents the data in a filterable dashboard hosted on GitHub Pages.

It was built as part of a competitive intelligence initiative for Saltbox (a warehouse/fulfillment company). The dashboard is styled to match the parent Saltbox Growth Engine site at `https://natwar86.github.io/saltbox/`.

## Live URLs

- **Dashboard**: https://natwar86.github.io/reviewtracker/
- **Repository**: https://github.com/natwar86/reviewtracker.git
- **Parent site**: https://natwar86.github.io/saltbox/

## Repository Structure

```
saltbox-2/
├── scraper.py              # Main scraper script (~807 lines)
├── requirements.txt        # Python dependencies
├── .gitignore              # Excludes venv, debug HTML, progress files, caches
├── PROJECT.md              # This file
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
    └── omnisend/               # Test app (not included in dashboard)
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

## The Scraper (`scraper.py`)

### Overview

A polite, resumable Shopify App Store review scraper. There is **no official API** for Shopify App Store reviews — scraping server-rendered HTML is the only option.

Reviews are at: `https://apps.shopify.com/{slug}/reviews?page={N}` (~10 reviews per page).

### Key Design Decisions

1. **Politeness first**: Random delays (configurable, default 4-8s), exponential backoff on errors (30s up to 5min cap), respects `Retry-After` headers. All 3 apps were scraped with `--min-delay 10 --max-delay 20` (slow mode) with zero rate limit errors.

2. **Resumable**: Progress saved to `progress.json` after every page. If interrupted (Ctrl+C, crash, etc.), re-running the same command picks up exactly where it left off.

3. **Store URL resolution**: Reviewer display names are probed as `{slug}.myshopify.com` subdomains using HEAD requests. If the subdomain redirects to a custom domain, that's captured as the store URL. Hit rate: ~38% across 601 unique reviewers (232 resolved).

4. **Deduplication**: Reviews are appended to a JSONL file during scraping, then deduplicated by body text (first 100 chars) when compiling final output.

5. **Sort order**: Final output is sorted by rating ascending (1-star first), then by date. This prioritizes low ratings for competitive intelligence.

### Python Dependencies

```
requests>=2.31.0      # HTTP client
beautifulsoup4>=4.12.0  # HTML parsing
lxml>=5.0.0           # Fast HTML parser backend
```

Install: `python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`

### CLI Usage

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

### Architecture

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

### Key Classes and Functions

**`PoliteFetcher(min_delay, max_delay)`** — HTTP client wrapper
- `fetch(url)` — GET with random delay, up to 5 retries, exponential backoff
- Respects 429/503 status codes and `Retry-After` headers
- Uses `requests.Session` for connection pooling

**`parse_reviews_page(html)`** — HTML parser using these CSS selectors:
```python
# Review blocks
review_blocks = soup.select("[data-merchant-review]")

# Per block:
review_id    = block.get("data-review-content-id", "")
rating       = block.select_one('div[role="img"][aria-label*="star"]')  # "N out of 5 stars"
date         = block.select_one(".tw-text-body-xs.tw-text-fg-tertiary")
body         = block.select_one("[data-truncate-review]:not([data-reply-id]) [data-truncate-content-copy]")
reviewer     = block.select_one(".tw-text-heading-xs span[title]")  # title attribute has full name
info_parent  = block.select_one("[class*='tw-order-1'][class*='tw-row-span']")  # location + usage
review_link  = block.select_one("[data-review-share-link]")  # data-review-share-link attribute
```

**`slugify_name(name)`** — Generates myshopify subdomain candidates:
- `"Make Believe Co."` → `["make-believe-co", "make-believe", "makebelieveco"]`
- Strips unicode, common business suffixes (inc, llc, co, etc.)
- Tries hyphenated, non-hyphenated, and "the-" stripped variants

**`resolve_store_url(reviewer_name, fetcher)`** — HEAD request to `{slug}.myshopify.com`:
- Follows redirects; if redirected away from myshopify.com, captures the custom domain
- Strips `/password` suffix (stores behind password pages)
- Upgrades `http://` to `https://`

**`compile_final_output(app_slug)`** — Post-processing:
- Reads JSONL, deduplicates by first 100 chars of body
- Sorts: rating ascending, then date ascending
- Writes `reviews.json` and `reviews.csv`

### Review Data Schema

Each review object in the JSON files:

```json
{
  "review_id": "1718756",
  "rating": 1.0,
  "date": "April 23, 2025",
  "body": "It's been a nightmare...",
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

### Current Data (as of March 8, 2026)

| App | Reviews | Store URLs Resolved | Resolution Rate |
|-----|---------|--------------------|----|
| ShipBob | 307 | 112 | 36.5% |
| ShipHero | 158 | 76 | 48.1% |
| ShipMonk | 143 | 44 | 30.8% |
| **Total** | **608** | **232** | **38.2%** |

There is also an `output/omnisend/` directory with test data (18 reviews) from initial development. This is NOT included in the dashboard.

## The Dashboard (`docs/index.html`)

### Overview

A single-page HTML file with all CSS and JS inline — no build step, no framework. Hosted on GitHub Pages from the `/docs` directory on `main` branch.

### Design System

The dashboard matches the Saltbox Growth Engine site (`https://natwar86.github.io/saltbox/`):

**Colors** (CSS variables in `:root`):
- `--paper: #f5f0e8` — Warm beige background
- `--ink: #1a1a1a` — Near-black text
- `--accent: #c2491d` — Rust/terracotta (primary accent, star fill color)
- `--card-bg: #fffdf8` — Off-white card backgrounds
- `--tag-bg: #f0e8d8` — Table header background
- `--border: #d4cec2` — Soft taupe borders
- `--muted: #8a8275` — Secondary text
- `--gap-red: #b83a2a` — Low rating indicator
- `--opp-green: #2a7d4f` — ShipHero badge color
- `--strategic-blue: #2a5a8a` — ShipBob badge color, store link color

**Typography**:
- `--serif: 'DM Serif Display'` — Headlines
- `--sans: 'DM Sans'` — Body text
- `--mono: 'DM Mono'` — Labels, data, monospace elements

**Visual details**:
- SVG fractal noise grain overlay (0.04 opacity, fixed position, z-index 9999)
- `fadeUp` entrance animations (staggered 0.1s-0.4s for sections, 0.015s per table row)
- Card-based layout with 8px border radius
- 80% max-width content area (100% on mobile < 768px)

### Features

1. **Data loading**: Fetches `data/{slug}.json` for each app via `fetch()` + `Promise.allSettled()`
2. **Stats strip**: Total reviews, avg rating, apps tracked, stores resolved, low ratings count
3. **Filters**:
   - App dropdown (populated dynamically from loaded data)
   - Star rating buttons (1-5 + All) — filters to exact rating
   - Location text search (substring match, case-insensitive, 250ms debounce)
   - Keyword search across review body (substring match, case-insensitive, 250ms debounce)
4. **Sorting**: Click any column header. Default: rating ascending. Toggles asc/desc on re-click.
5. **Low rating highlight**: Rows with rating 1-3 get a red left border and tinted background
6. **Expandable review text**: Truncated to 2 lines by default, click to expand
7. **Pagination**: 50 reviews per page with page number buttons
8. **App badges**: Color-coded per app (ShipBob=blue, ShipHero=green, ShipMonk=rust)
9. **Store links**: Clickable links to resolved store URLs, displayed as clean domain names

### Adding a New App to the Dashboard

1. Run the scraper: `python scraper.py new-app-slug --min-delay 10 --max-delay 20`
2. Copy the output: `cp output/new-app-slug/reviews.json docs/data/new-app-slug.json`
3. Edit `docs/index.html` line 674: add the slug to the `DATA_FILES` array:
   ```js
   const DATA_FILES = ['shipbob', 'shiphero', 'shipmonk', 'new-app-slug'];
   ```
4. Optionally add a CSS badge color for `.app-new-app-slug` in the `<style>` block
5. Commit and push — GitHub Pages will auto-deploy

### Footer

The footer matches the parent Saltbox site:
- Name: Natwar Maheshwari
- Context: "Assessment for Director of Growth at Saltbox, March 2026"
- Contact: natwar86@gmail.com, LinkedIn, GitHub
- Tagline: "Built with AI as a force multiplier"

## Deployment

- **Hosting**: GitHub Pages, served from `/docs` directory on `main` branch
- **Repository**: `https://github.com/natwar86/reviewtracker.git`
- **Branch**: `main` (only branch)
- **No build step**: Just push to `main` and GitHub Pages serves `docs/index.html` directly

To deploy changes:
```bash
git add docs/index.html docs/data/whatever.json
git commit -m "description of change"
git push origin main
```

GitHub Pages typically deploys within 1-2 minutes after push.

## Known Limitations

1. **Shopify HTML structure may change**: The scraper relies on specific CSS selectors (`[data-merchant-review]`, `.tw-text-heading-xs`, etc.) that could break if Shopify redesigns their review pages. If parsing breaks, use `--save-html` and inspect the HTML in `output/{app}/debug_html/`.

2. **Store URL resolution is imperfect**: Only ~38% of reviewers have resolvable myshopify.com subdomains. Many stores use names that don't match their subdomain, or have closed.

3. **Dates are human-readable strings**: The `date` field is stored as-is from Shopify (e.g. "April 23, 2025"), not ISO format. The dashboard's JS `parseDate()` handles this via `new Date(dateStr)`.

4. **No automatic re-scraping**: The data is static. To update, re-run the scraper manually. Delete `output/{app}/progress.json` to force a full re-scrape (otherwise it resumes from where it left off).

5. **Rating is float**: Stored as `1.0` not `1`. The dashboard compares with `===` against integers from the filter buttons, which works because JS `1.0 === 1` is `true`.
