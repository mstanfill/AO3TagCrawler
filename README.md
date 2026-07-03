# AO3 Tag Crawler

A Python tool that scrapes the tag list from [Archive of Our Own (AO3)](https://archiveofourown.org/tags)
and, for each tag, collects **metadata only** (no fic body text) for the 100 most
recent works. Both the tag list and the work metadata are saved to CSV.

Built on the same pipeline design as
[mstanfill/AO3MetadataScraper](https://github.com/mstanfill/AO3MetadataScraper): explicit,
resumable steps, rate-limited requests, and retry with exponential backoff.

---

## Features

| Feature | Detail |
|---|---|
| **Tag discovery** | Scrapes the tag cloud at `archiveofourown.org/tags` itself — no need to hand-supply a URL |
| **Metadata only** | Never downloads fic body text |
| **Three explicit steps** | Collect tags → collect work IDs per tag → collect metadata. Each step writes a plain CSV you can inspect before the next step runs |
| **Resumable** | Steps can be run independently from saved CSVs; `--resume` skips works already scraped |
| **Robust error handling** | Retries timeouts, HTTP 429, 5xx, and 525 with exponential back-off (15s → 30s → 60s → 120s → 240s) |
| **AO3 ToS compliant** | Enforces a minimum 5-second delay between all requests |

---

## Output columns

**`ao3_tags.csv`** (Step 1): `tag_name`, `tag_href`, `tag_url`

**`ao3_tag_work_ids.csv`** (Step 2): `tag_name`, `work_id`

**`ao3_tag_metadata.csv`** (Step 3): `tag`, `work_id`, `title`, `author`, `rating`,
`warnings`, `category`, `fandom`, `relationship`, `character`, `additional_tags`,
`language`, `series`, `published`, `status`, `status_date`, `words`, `chapters`,
`comments`, `kudos`, `bookmarks`, `hits`, `summary`

---

## Requirements

- Python 3.10 or newer
- See `requirements.txt`

## Installation

```bash
git clone https://github.com/mstanfill/AO3TagCrawler.git
cd AO3TagCrawler
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Usage

### Full pipeline (default)

```bash
python ao3_tag_scraper.py
```

Fetches the tag list from `archiveofourown.org/tags`, then for every tag found there
collects the 100 most recent works' metadata:

```
ao3_tags.csv            <- Step 1: tag_name, tag_href, tag_url
ao3_tag_work_ids.csv    <- Step 2: tag_name, work_id
ao3_tag_metadata.csv    <- Step 3: one metadata row per work
```

### Change how many works per tag

```bash
python ao3_tag_scraper.py --limit-per-tag 25
```

### Only collect the tag list

```bash
python ao3_tag_scraper.py --tags-only
```

### Run steps individually

```bash
# Step 1 only, review ao3_tags.csv, then continue from it:
python ao3_tag_scraper.py --tags-only
python ao3_tag_scraper.py --step2 ao3_tags.csv

# Step 3 only, from an existing work-IDs file:
python ao3_tag_scraper.py --step3 ao3_tag_work_ids.csv --out ao3_tag_metadata.csv
```

### Resume an interrupted Step 3

```bash
python ao3_tag_scraper.py --step3 ao3_tag_work_ids.csv --out ao3_tag_metadata.csv --resume
```

`--resume` reads the existing metadata CSV, skips `(tag, work_id)` pairs already
present, and appends only new rows.

### Custom output filenames / User-Agent

```bash
python ao3_tag_scraper.py \
  --tags-out tags.csv --ids-out ids.csv --out metadata.csv \
  --header "MyProject/1.0; me@email.com"
```

### All options

```
--limit-per-tag N   Most recent works to collect per tag (default: 100)
--tags-out FILE      Step 1 output (default: ao3_tags.csv)
--ids-out FILE        Step 2 output (default: ao3_tag_work_ids.csv)
--out FILE             Step 3 metadata output (default: ao3_tag_metadata.csv)
--errors-out FILE       Step 3 errors output (default: errors_<--out>)
--tags-only              Run Step 1 only, then stop
--step2 TAGS_FILE         Skip Step 1; load tags from an existing tags CSV
--step3 IDS_FILE           Skip Steps 1-2; load (tag, work_id) pairs from a CSV
--resume                    Step 3: skip pairs already present in --out
--header AGENT                User-Agent suffix, e.g. "MyProject/1.0; me@email.com"
-h, --help
```

## How it works

```
Step 1 - Collect the tag list          (1 request)
│
│   GET archiveofourown.org/tags
│   Parses <ul class="tags cloud index group"> for <a class="tag"> links.
│   Writes tag_name, tag_href, tag_url to ao3_tags.csv.
│
Step 2 - Collect work IDs per tag      (paged, per tag)
│
│   For each tag, GETs {tag_url}?page=N&view_adult=true.
│   AO3 renders each work as <li id="work_NNNNNNN" class="work blurb group">.
│   Pages until --limit-per-tag unique IDs are collected or a page is empty.
│   Appends tag_name, work_id to ao3_tag_work_ids.csv.
│
Step 3 - Collect metadata               (1 request per work)
│
│   GETs /works/{id}?view_adult=true for each (tag, work_id) pair.
│   Parses <dl class="work meta group"> for tags and stats, plus title/author/summary.
│   Streams one CSV row per work to disk immediately after each fetch.
│   Works that fail are logged to errors_ao3_tag_metadata.csv.
```

### Error handling

`fetch()` retries with exponential back-off on transient errors:

| Error | Behaviour |
|---|---|
| Read timeout | Retry — 60s timeout per request |
| HTTP 429 | Retry — rate-limited by AO3 |
| HTTP 525 | Retry — Cloudflare SSL handshake error (transient) |
| HTTP 500/502/503/504 | Retry — server errors |
| HTTP 403 | Log and skip |
| HTTP 404 | Log and skip |

Back-off schedule: 15s → 30s → 60s → 120s → 240s, then give up after 5 attempts.

## AO3 terms of service

AO3 asks that scraping tools wait between requests to avoid overloading their
servers. This tool enforces a **minimum 5-second delay** (`REQUEST_DELAY`) between
every request. Please do not reduce this value.

See: [archiveofourown.org/TOS](https://archiveofourown.org/TOS)

## License

CC BY-NC 4.0, matching [mstanfill/AO3MetadataScraper](https://github.com/mstanfill/AO3MetadataScraper).
