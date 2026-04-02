# Signal Listener — Build Spec

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

## Architecture Overview

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

## Common Signal Schema

Every signal from every source should normalize to this structure:

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
    "competitor_mentioned": ["ShipBob", "ShipMonk", etc.],
    "market": "atlanta | dallas | denver | ... | unknown",
    "intent": "switching | exploring | complaining | asking | hiring | scaling",
    "summary": "AI-generated one-line summary of the signal"
  }
}
```

## Signal Sources — Detailed Specs

### 1. Shopify App Store Reviews

**Status**: v0.1 prototype exists at https://natwar86.github.io/reviewtracker/

**Apps to monitor**:
| App | Shopify URL | Current Reviews |
|-----|-------------|-----------------|
| ShipBob | apps.shopify.com/shipbob | 278 |
| ShipHero | apps.shopify.com/shiphero | 114 |
| ShipMonk | apps.shopify.com/shipmonk | 96 |
| Fulfillrite | apps.shopify.com/fulfillrite | 35 |
| Parsel (own) | apps.shopify.com/saltbox | 8 |

**Collection method**:
- Shopify doesn't have a public review API. Scrape the review pages.
- Each app's reviews are at `apps.shopify.com/{app-slug}/reviews`
- Paginated — need to handle pagination
- Extract: reviewer name, store name (often in review text), rating, date, review body
- Schedule: daily scrape, diff against previous to find new reviews

**AI classification focus**:
- 1-2 star reviews = hot signals
- Extract specific pain points: "hidden fees", "lost inventory", "slow shipping", "no control", "terrible support"
- If reviewer mentions their store name or URL, that's a directly contactable lead

---

### 2. Reddit

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

### 3. Google Maps Reviews

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

### 4. Job Postings

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

### 5. Funding Announcements

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

### 6. Twitter/X

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

### 7. Amazon Seller Forums

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

### 8. Trustpilot / BBB

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

### 9. LinkedIn Posts

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

### 10. Macro Triggers

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

### 11. Shopify Community Forums

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

### 12. YouTube Comments

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

## AI Classification Layer

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

## Dashboard Requirements

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

## Tech Stack Suggestions

- **Frontend**: React or Next.js (dashboard) — or keep it simple with a single HTML file like the v0.1 prototype
- **Backend**: Node.js or Python (data collection scripts)
- **Database**: Supabase (free tier) or SQLite for prototype
- **Scheduling**: cron jobs, or Vercel cron, or GitHub Actions on schedule
- **AI**: Anthropic API (Claude Haiku for classification) or OpenAI API (GPT-4o-mini)
- **Hosting**: GitHub Pages for frontend, Vercel/Railway for backend scripts
- **Alerts**: Email via Resend/SendGrid, Slack webhooks

## Build Priority

### Phase 1 (v0.2 — extend existing prototype)
1. Extend Shopify review scraper to run on schedule (not just static data)
2. Add Reddit API integration
3. Add AI sentiment classification
4. Basic dashboard with filtering

### Phase 2
5. Google Maps API integration
6. Twitter/X API integration
7. Trustpilot scraper
8. Hot signal alerts (email/Slack)

### Phase 3
9. Job posting monitoring
10. Crunchbase funding announcements
11. YouTube Data API
12. Amazon Seller Forums scraper
13. Shopify Community monitoring
14. Macro triggers (RSS + Google Alerts)

### Phase 4
15. Trend analytics
16. Content brief generator
17. Cross-source deduplication
18. LinkedIn integration (semi-automated)

## Existing Work

- **v0.1 prototype**: https://natwar86.github.io/reviewtracker/
- **Repo**: https://github.com/natwar86/reviewtracker (assumed)
- **Data collected**: Shopify App Store reviews from ShipBob, ShipHero, ShipMonk
- **Format**: static HTML dashboard displaying review data

## Key Design Principle

This is a **growth marketing tool**, not an enterprise monitoring platform. Keep it scrappy and functional. A working prototype that captures 80% of the value is better than a polished system that takes months to build. The goal is to demonstrate that this approach works and generates real pipeline — not to build production infrastructure on day one.
