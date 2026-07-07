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

## Notebook

`ao3_tag_scraper.ipynb` is a Jupyter notebook version of the same pipeline. Open it in
VS Code, JupyterLab, or Classic Jupyter. Edit the Configuration cell to set
`LIMIT_PER_TAG`, output filenames, and `RESUME`, then run all cells in order.

> **Kernel restart required after updating:** if you've previously run an older
> version of the notebook in the same session, do **Kernel → Restart Kernel and Run
> All Cells**. Stale function definitions from a previous run persist in memory until
> the kernel is restarted.

## Visualization

`ao3_tag_visualizer.py` (and its notebook twin, `ao3_tag_visualizer.ipynb`) reads an
existing `ao3_tag_metadata.csv` and visualizes connections between the seed tag and
each work's `rating`, `warnings`, `category`, `fandom`, and `additional_tags`. It has
**no network dependency** — it only reads a local CSV.

| Feature | Detail |
|---|---|
| **Interactive network graph** | Bipartite graph: seed tags <-> attribute values, edges weighted by co-occurrence count. Self-contained HTML — no internet connection needed to view it |
| **Co-occurrence heatmaps** | One per field: rows = seed tags, columns = attribute values, cell (color and displayed number) = %% of that seed tag's works |
| **High-cardinality filtering** | `fandom` and `additional_tags` are filtered to their top-N most frequent values overall (`--top-fandoms`, `--top-additional-tags`) before either visualization is built |
| **Configurable thresholds** | `--min-count` (or `--min-proportion`, mutually exclusive) drops noisy edges/cells; `--top-seed-tags` limits rows/nodes to the highest-volume seed tags |
| **Tag-pair co-occurrence (opt-in)** | `--tag-pairs` computes statistical lift/PMI between pairs of `fandom`/`relationship`/`character`/`additional_tags` tags across the whole dataset — which pairs co-occur more or less than chance — and renders a second network graph + heatmap |

### Output files

**`ao3_tag_network.html`** — self-contained interactive network graph (pan/zoom/drag/hover)

**`heatmaps/heatmap_<field>.png`** — one PNG per field in `rating`, `warnings`,
`category`, `fandom`, `additional_tags`

**`ao3_tag_pair_network.html`** / **`heatmaps/heatmap_tag_pairs.png`** — only written
with `--tag-pairs`: tag-to-tag network graph and heatmap of lift/PMI values

### Usage

```bash
python ao3_tag_visualizer.py
```

Reads `ao3_tag_metadata.csv` and writes `ao3_tag_network.html` plus
`heatmaps/heatmap_<field>.png` for each field.

```bash
# Adjust top-N filtering and noise threshold
python ao3_tag_visualizer.py --top-fandoms 10 --top-additional-tags 20 --min-count 3

# Alternative to --min-count: keep a (tag, value) pair only if it appears in
# at least 10% of that seed tag's own works -- treats a low-volume tag
# fairly instead of by raw count. Mutually exclusive with --min-count.
python ao3_tag_visualizer.py --min-proportion 0.1

# Only the network, or only the heatmaps
python ao3_tag_visualizer.py --network-only
python ao3_tag_visualizer.py --heatmaps-only

# Also compute tag-pair co-occurrence (lift/PMI) across fandom/relationship/
# character/additional_tags -- which pairs of tags co-occur statistically more
# or less than chance. Off by default (a full pairwise computation would
# otherwise slow down every run); --network-only/--heatmaps-only apply to
# these outputs too.
python ao3_tag_visualizer.py --tag-pairs

# Adjust the tag-pair thresholds: only the top 60 tags by document frequency,
# require at least 5 co-occurrences, and widen the "most/least likely" bands
python ao3_tag_visualizer.py --tag-pairs --top-tags 60 --min-pair-count 5 --min-pmi 1.5 --max-pmi -1.5
```

`--tag-pairs` answers a different question than the rest of this tool: not "which
attribute values does a seed tag associate with", but "which pairs of tags -- pooled
across `fandom`/`relationship`/`character`/`additional_tags` -- statistically tend to
co-occur (or avoid each other) more than chance would predict". Raw co-occurrence
counts conflate "both tags are individually common" with "these two tags are actually
associated"; lift and PMI correct for that:

- `lift(A, B) = P(A, B) / (P(A) * P(B))`
- `pmi(A, B) = log2(lift(A, B))`

`pmi > 0` means the pair co-occurs *more* than chance ("most likely"), `pmi < 0` means
*less* than chance ("least likely"), `pmi == 0` means independence. `--min-pmi` and
`--max-pmi` are two independently adjustable thresholds -- one per tail -- that drop
the "boring middle" near independence; `--min-pair-count` runs first and filters out
low-sample coincidences (e.g. two tags that only ever appear together on a single
work) whose lift would otherwise look enormous but isn't statistically meaningful.

### All options

```
--input FILE              Metadata CSV to read (default: ao3_tag_metadata.csv)
--top-fandoms N           Keep only the top N most frequent fandoms overall (default: 20)
--top-additional-tags N   Keep only the top N most frequent additional tags overall (default: 40)
--min-count N             Drop edges/cells below this co-occurrence count (default: 2).
                          Mutually exclusive with --min-proportion
--min-proportion F        Alternative to --min-count: drop a (seed tag, value) edge/cell
                          below this fraction (0.0-1.0) of that seed tag's total works
                          (default: disabled, --min-count applies instead). Mutually
                          exclusive with --min-count
--top-seed-tags N         Only include the N seed tags with the most works (default: all)
--network-out FILE        Interactive network HTML output (default: ao3_tag_network.html)
--heatmap-out-dir DIR     Directory for heatmap PNGs (default: heatmaps)
--network-only            Only build the network, skip heatmaps
--heatmaps-only           Only build heatmaps, skip the network
--tag-pairs               Also compute/render tag-pair co-occurrence statistics
                          (lift/PMI) across fandom/relationship/character/
                          additional_tags (default: off)
--top-tags N              Top N tags overall, by document frequency (default: 40)
--min-pair-count N        Drop pairs with fewer co-occurrences than this (default: 2)
--min-pmi F               "Most likely" threshold: keep pairs with pmi >= this (default: 1.0)
--max-pmi F               "Least likely" threshold: keep pairs with pmi <= this (default: -1.0)
--tag-pair-network-out FILE    Tag-pair network HTML output
                                (default: ao3_tag_pair_network.html)
--tag-pair-heatmap-out FILE    Tag-pair heatmap PNG output
                                (default: heatmaps/heatmap_tag_pairs.png)
-h, --help
```

### Notebook

`ao3_tag_visualizer.ipynb` is a Jupyter notebook version of the same tool, structured
like `ao3_tag_scraper.ipynb` — edit the Configuration cell, then run all cells in
order. The network graph and heatmaps render inline in addition to being saved to disk.

## Tag Analysis

`ao3_tag_analysis.py` (and its notebook twin, `ao3_tag_analysis.ipynb`) reads an
existing `ao3_tag_metadata.csv` and runs two further analyses, beyond
`ao3_tag_visualizer.py`'s bipartite network and tag-pair lift/PMI network. It has
**no network dependency** — it only reads a local CSV.

| Feature | Detail |
|---|---|
| **additional_tags frequency ranking** | Three categories: most frequent `additional_tags` values that are also a seed tag, most frequent values that aren't, and least frequent values (excluding one-off singletons) |
| **Cross-field hierarchical clustering** | Pools labels from *all* metadata fields (`rating`, `warnings`, `category`, `fandom`, `relationship`, `character`, `additional_tags`) and clusters them by lift/PMI similarity — which labels of any kind tend to appear together — rendered as a dendrogram + reordered heatmap plus a discrete cluster-membership CSV |

Both analyses run by default; `--frequency-only`/`--clusters-only` narrow it to one.

### Output files

**`ao3_additional_tags_frequency.csv`** — each row tagged by `rank_type`:
`most_frequent_seed_tag` (an `additional_tags` value that's also a **seed
tag** — the AO3 tag actually searched to find each work, i.e. the `tag`
column in `ao3_tag_metadata.csv` — partly reflecting the scrape's own search
bias rather than a genuinely emergent discovery), `most_frequent_non_seed_tag`
(the same ranking restricted to values that were never searched for directly),
or `least_frequent` (drawn from the full `additional_tags` pool, seed tags
included, unchanged by the seed/non-seed split above)

**`heatmaps/heatmap_clusters.png`** — a seaborn clustermap: rows/columns reordered
by hierarchical clustering, with a dendrogram tree on each axis showing nested
groupings, cells colored by PMI

**`ao3_tag_clusters.csv`** — discrete cluster membership (`tag_id`, `field`,
`label`, `cluster_id`), cut from the same dendrogram the heatmap plots, so the two
outputs are always consistent with each other

### Usage

```bash
python ao3_tag_analysis.py
```

Reads `ao3_tag_metadata.csv` and writes `ao3_additional_tags_frequency.csv`,
`heatmaps/heatmap_clusters.png`, and `ao3_tag_clusters.csv`.

```bash
# Only the frequency ranking, or only the clustering
python ao3_tag_analysis.py --frequency-only
python ao3_tag_analysis.py --clusters-only

# Adjust the clustering: more tags pooled, fewer/more discrete clusters, a
# different scipy linkage method
python ao3_tag_analysis.py --top-tags 100 --n-clusters 15 --cluster-method ward
```

The clustering pipeline pools all seven metadata fields into one namespaced label
space (`f"{field}::{value}"`) and computes pairwise lift/PMI, the same statistic
`--tag-pairs` uses:

- `lift(A, B) = P(A, B) / (P(A) * P(B))`
- `pmi(A, B) = log2(lift(A, B))`

...then hierarchically clusters the resulting tag × tag PMI matrix to group labels
by similarity. `--min-pair-count` drops low-sample coincidences before clustering
(a pair that only ever co-occurs once has a meaningless but enormous lift), but
unlike `--tag-pairs`' `--min-pmi`/`--max-pmi` thresholds, clustering keeps the full
similarity structure, including near-zero PMI values — that's what a dendrogram
needs.

### All options

```
--input FILE                 Metadata CSV to read (default: ao3_tag_metadata.csv)
--frequency-top-n N          Most frequent additional_tags to report, per category --
                              seed tags and non-seed tags each get up to this many
                              (default: 20)
--frequency-bottom-n N       Least frequent additional_tags to report (default: 20)
--frequency-min-count N      Floor for "least frequent" -- excludes values with
                              fewer works than this, e.g. default 2 excludes
                              one-off singletons (default: 2)
--frequency-out FILE         Frequency ranking CSV output
                              (default: ao3_additional_tags_frequency.csv)
--top-tags N                 Top N tags overall, pooled across all 7 metadata
                              fields, by document frequency, before clustering
                              (default: 60)
--min-pair-count N           Drop pairs co-occurring fewer than this many times
                              before clustering (default: 2)
--n-clusters N                Cut the dendrogram into this many discrete clusters
                              (default: 10)
--cluster-method {average,complete,ward,single}   scipy linkage method (default: average)
--heatmap-out-dir DIR        Directory for the cluster heatmap PNG (default: heatmaps)
--cluster-heatmap-out FILE   Cluster heatmap PNG output
                              (default: <--heatmap-out-dir>/heatmap_clusters.png)
--clusters-out FILE          Cluster-membership CSV output (default: ao3_tag_clusters.csv)
--frequency-only             Only compute the frequency ranking, skip clustering
--clusters-only              Only compute clustering, skip the frequency ranking
-h, --help
```

### Notebook

`ao3_tag_analysis.ipynb` is a Jupyter notebook version of the same tool, structured
like `ao3_tag_visualizer.ipynb` — edit the Configuration cell, then run all cells in
order. The clustermap and cluster table render inline in addition to being saved to
disk.

## AO3 terms of service

AO3 asks that scraping tools wait between requests to avoid overloading their
servers. This tool enforces a **minimum 5-second delay** (`REQUEST_DELAY`) between
every request. Please do not reduce this value.

See: [archiveofourown.org/TOS](https://archiveofourown.org/TOS)

## License

CC BY-NC 4.0, matching [mstanfill/AO3MetadataScraper](https://github.com/mstanfill/AO3MetadataScraper).
