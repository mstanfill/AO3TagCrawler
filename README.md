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
| **Tag discovery** | Scrapes the tag cloud at `archiveofourown.org/tags` itself — no need to hand-supply a URL. Or start from specific tags instead, via `--tag`/`--tag-url` |
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

### Start from specific tags instead of the tag cloud

Skip Step 1's `/tags` scrape entirely and seed the pipeline with chosen tags,
by name and/or by works-page URL (both flags are repeatable and can be mixed):

```bash
python ao3_tag_scraper.py \
  --tag-url "https://archiveofourown.org/tags/Choose%20Not%20To%20Use%20Archive%20Warnings/works"

python ao3_tag_scraper.py --tag "Creator Chose Not To Use Archive Warnings" --tag "Angst"
```

Names are converted to URLs with AO3's tag-name escaping (and vice versa), so
relationship-style names work: `--tag "Bob/Carol"` targets
`/tags/Bob*s*Carol/works`. A tag *landing-page* URL (without `/works`) is also
accepted. The same `ao3_tags.csv` is written either way, so `--step2`/reruns
behave identically to a cloud-scraped run.

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
--tag NAME           Start from this tag instead of the /tags cloud (repeatable;
                      AO3's tag-name escaping is applied, e.g. Bob/Carol ->
                      /tags/Bob*s*Carol/works)
--tag-url URL         Start from this tag works-page or landing-page URL
                       (repeatable; the tag name is recovered from the URL)
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
`LIMIT_PER_TAG`, output filenames, and `RESUME` (and optionally `START_TAGS`, a list
of tag names and/or works-page URLs that bypasses Step 1's tag-cloud scrape), then
run all cells in order.

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

**`heatmaps/heatmap_<field>.png` / `.csv` / `.html`** — one set per field in
`rating`, `warnings`, `category`, `fandom`, `additional_tags`. Each heatmap is
written in three formats: the PNG image, the exact matrix as CSV (for
sorting/filtering in Excel, Sheets, or pandas — the PNG becomes unscannable
once there are hundreds of seed-tag rows), and a self-contained sortable HTML
table with the same color scale, sticky row/column headers, click-to-sort on
any column, and a live search box for seed tags. Note: a same-name cell (seed
tag `Angst` × displayed tag `Angst`) below 100% is not a bug — see
[Tag Wrangling](#tag-wrangling)

**`ao3_tag_pair_network.html`** / **`heatmaps/heatmap_tag_pairs.png` / `.csv` /
`.html`** — only written with `--tag-pairs`: tag-to-tag network graph and
heatmap of lift/PMI values, the heatmap in the same three formats
(never-co-occurring pairs stay blank in all three, distinct from a meaningful 0)

### Usage

```bash
python ao3_tag_visualizer.py
```

Reads `ao3_tag_metadata.csv` and writes `ao3_tag_network.html` plus
`heatmaps/heatmap_<field>.png` (and its `.csv`/`.html` companions) for each field.

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
--heatmap-out-dir DIR     Directory for heatmap outputs (PNG + CSV + sortable
                           HTML per heatmap) (default: heatmaps)
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
| **Cross-field community detection** | Pools labels from *all* metadata fields (`rating`, `warnings`, `category`, `fandom`, `relationship`, `character`, `additional_tags`) — or every tag with `--all-tags` — and groups them by lift/PMI similarity via graph-based community detection (Louvain) — which labels of any kind tend to appear together — rendered as an interactive network graph plus a discrete cluster-membership CSV, optionally enforcing a minimum cluster size via `--min-cluster-size` |

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

**`ao3_tag_cluster_network.html`** — an interactive network graph (same
pyvis/vis-network machinery as `--tag-pairs`' network): every kept tag is a node,
colored/grouped by its final cluster, with checkboxes to filter by cluster and a
tag picker to narrow to specific tags and their direct connections. An edge is
drawn between any pair with `pmi > 0` (co-occurs more than chance). Unlike
`--tag-pairs`' network, node positions are precomputed (each cluster arranged
on its own circle, clusters placed on a grid) rather than settled live by
client-side physics — at `--all-tags` scale (tens of thousands of nodes) a
live force simulation is what makes a browser tab hang, independent of file
size

**`ao3_tag_cluster_meta_network.html`** — the readable summary view of the same
data: one node per **cluster** (dozens instead of tens of thousands), sized by
tag count and labeled with the cluster's top co-occurring fandom (e.g.
`7: Harry Potter`), with an edge wherever positive-PMI tag pairs cross two
clusters (width scales with how many). Hover a node for the cluster's tag/work
counts and full top-fandom breakdown; hover an edge for the link count and mean
PMI

**`ao3_tag_clusters.csv`** — discrete cluster membership (`tag_id`, `field`,
`label`, `cluster_id`), built from the same community-detection result the network
graph is colored by, so the two outputs are always consistent with each other

**`<name>.gexf`** *(opt-in, via `--gexf-out`)* — the full tag-level graph in GEXF
format for [Gephi](https://gephi.org/), which is built for exploring graphs this
size: File → Open the `.gexf`, then Appearance → Partition by `cluster_id` to
color by cluster, and Layout → ForceAtlas2. Nodes carry `label`/`field`/
`cluster_id`; edges carry `weight` (=PMI), `lift`, and `joint_count`

### Usage

```bash
python ao3_tag_analysis.py
```

Reads `ao3_tag_metadata.csv` and writes `ao3_additional_tags_frequency.csv`,
`ao3_tag_cluster_network.html`, `ao3_tag_cluster_meta_network.html`, and
`ao3_tag_clusters.csv`.

```bash
# Only the frequency ranking, or only the clustering
python ao3_tag_analysis.py --frequency-only
python ao3_tag_analysis.py --clusters-only

# Adjust the clustering: more tags pooled, a higher Louvain resolution for
# more/smaller communities
python ao3_tag_analysis.py --top-tags 100 --cluster-resolution 1.5

# Cluster every tag (no top-N truncation) and require at least 3 tags per
# cluster -- undersized communities are merged into their strongest-connected
# neighbor (or the largest remaining community, if fully isolated)
python ao3_tag_analysis.py --all-tags --min-cluster-size 3
```

The clustering pipeline pools all seven metadata fields into one namespaced label
space (`f"{field}::{value}"`) and computes pairwise lift/PMI, the same statistic
`--tag-pairs` uses:

- `lift(A, B) = P(A, B) / (P(A) * P(B))`
- `pmi(A, B) = log2(lift(A, B))`

...then groups labels into communities via graph-based community detection
(networkx's Louvain algorithm), operating directly on the sparse co-occurrence
graph — only real `pmi > 0` edges are ever materialized, never a dense tag × tag
matrix, so this scales to tens of thousands of tags the way a dendrogram (which
needs a full pairwise-distance matrix) can't. `--min-pair-count` drops low-sample
coincidences before clustering (a pair that only ever co-occurs once has a
meaningless but enormous lift).

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
                              fields, by document frequency, before clustering.
                              Overridden by --all-tags (default: 60)
--all-tags                   Cluster using every tag from all 7 metadata fields,
                              ignoring --top-tags (default: off)
--min-pair-count N           Drop pairs co-occurring fewer than this many times
                              before clustering (default: 2)
--cluster-resolution F       Louvain resolution -- higher means more, smaller
                              communities; lower means fewer, larger ones
                              (default: 1.0)
--min-cluster-size N         Merge communities smaller than this into another
                              community (their strongest-connected neighbor, or
                              the largest remaining community if fully isolated)
                              (default: 1, no minimum)
--cluster-network-out FILE   Cluster network HTML output
                              (default: ao3_tag_cluster_network.html)
--cluster-meta-network-out FILE   Cluster meta-network HTML output -- one node
                              per cluster, labeled with its top fandom
                              (default: ao3_tag_cluster_meta_network.html)
--gexf-out FILE              Also write the full tag-level cluster graph as GEXF
                              for Gephi. Off by default -- the XML is large and
                              slow to write at --all-tags scale (default: not
                              written)
--clusters-out FILE          Cluster-membership CSV output (default: ao3_tag_clusters.csv)
--frequency-only             Only compute the frequency ranking, skip clustering
--clusters-only              Only compute clustering, skip the frequency ranking
-h, --help
```

### Notebook

`ao3_tag_analysis.ipynb` is a Jupyter notebook version of the same tool, structured
like `ao3_tag_visualizer.ipynb` — edit the Configuration cell, then run all cells in
order. The cluster network and cluster table render inline in addition to being
saved to disk.

## Fandom Labeling

`ao3_tag_fandom_labels.py` labels an existing tag CSV (e.g. `ao3_tag_clusters.csv`,
from `ao3_tag_analysis.py`) with the fandom(s) each tag is associated with. For
every `tag_id`, it finds every work containing that tag in `ao3_tag_metadata.csv`
and reports the top N fandoms those works belong to, by co-occurrence percentage
— computed directly from the scrape, not guessed from the tag's name. A
fandom-field tag trivially labels as itself near 100%; a cross-cutting trope tag
(e.g. `additional_tags::Angst`) shows a spread across whichever fandoms it
actually appears in.

It also writes a **per-cluster summary**: for each `cluster_id`, it pools every
work containing *any* of the cluster's tags (counted once per cluster, no matter
how many of its tags a work matches) and ranks the fandoms of those works by
percent of works — which fandoms a whole cluster is about, rather than tag by tag.

### Usage

```bash
python ao3_tag_fandom_labels.py
```

Reads `ao3_tag_metadata.csv` and `ao3_tag_clusters.csv`, and writes:

**`ao3_tag_clusters_with_fandoms.csv`** — every column from the input CSV, plus a
new `top_fandoms` column, e.g. `Fandom A (75%), Fandom B (25%)`. The input
`--clusters-csv` is never modified in place.

**`ao3_cluster_fandoms.csv`** — one row per cluster: `cluster_id`, `n_tags` (the
cluster's distinct tags), `n_works` (distinct works containing any of them), and
`top_fandoms` in the same `Fandom A (62%), …` format, percentages out of the
cluster's whole work pool. Skipped with a note if the input CSV has no
`cluster_id` column.

```bash
# Label a different CSV, with a different N and column name
python ao3_tag_fandom_labels.py --clusters-csv my_tags.csv --top-n 5 --column-name fandoms
```

### All options

```
--input FILE                Metadata CSV to compute co-occurrence from
                             (default: ao3_tag_metadata.csv)
--clusters-csv FILE         Tag CSV to label -- must have a tag_id column
                             (default: ao3_tag_clusters.csv)
--top-n N                   Number of top co-occurring fandoms to report, per tag
                             and per cluster (default: 3)
--column-name NAME          Name for the new fandom-label column (default: top_fandoms)
--out FILE                  Labeled CSV output (default: ao3_tag_clusters_with_fandoms.csv)
--cluster-fandoms-out FILE  Per-cluster fandom summary CSV output
                             (default: ao3_cluster_fandoms.csv)
-h, --help
```

A `tag_id` present in `--clusters-csv` but not found in `--input` (a stale or
mismatched pairing) gets an empty label rather than an error, with a warning
printed summarizing how many tags were affected.

### Notebook

`ao3_tag_fandom_labels.ipynb` is a Jupyter notebook version of the same tool,
structured like `ao3_tag_analysis.ipynb` — edit the Configuration cell, then
run all cells in order. The labeled table renders inline in addition to being
saved to disk.

## Tag Wrangling

Browsing an AO3 canonical tag page (the scraper's seed tags) returns works
tagged with the canonical tag **or any synonym/subtag wranglers merged into
it** — but the scraped metadata records each work's tags as the author typed
them. That's why a heatmap's same-name cell (seed tag `Angst` × displayed tag
`Angst`) can read 47.5% rather than 100%: it measures how often authors
literally typed the canonical tag. Zeroing it would falsely claim no works
under `Angst` are tagged `Angst`; forcing it to 100 would fabricate a number
the scraped data contradicts.

`ao3_tag_wrangling.py` makes the measurement explicit. It has **no network
dependency** — it only reads local CSVs.

### Output files

**`ao3_seed_tag_literal_usage.csv`** — one row per seed tag: `n_works`,
`literal_works` (works displaying the seed tag itself, case-insensitively, in
*any* metadata field — a seed tag can be a fandom/relationship/character, and
AO3 tags are case-insensitively unique so a case variant is the same tag),
`literal_pct`, and `wrangled_pct`. The two percentages always sum to 100 — a
strict two-way partition of each seed tag's works.

**`ao3_seed_tag_synonym_breakdown.csv`** *(only when `--synonyms-csv` exists)*
— names the exact form each work used: `seed_tag, matched_via, n_works, pct`
where `matched_via` is `literal`, the exact synonym/subtag name, or
`unidentified` (a wrangling relation the CSV doesn't cover). A non-literal
work counts under every known relation it displays, so a seed tag's
percentages can sum past 100 (same semantics as the fandom-label outputs).

The relations CSV format is `seed_tag, relation, related_tag` with `relation`
in `synonym`/`subtag` (other values are ignored). It can be built by hand
today; a scraper step that collects AO3's own synonym lists (one request per
tag landing page) is a possible future addition.

### Usage

```bash
python ao3_tag_wrangling.py
```

### All options

```
--input FILE          Metadata CSV to read (default: ao3_tag_metadata.csv)
--synonyms-csv FILE   Wrangling relations CSV; the breakdown is skipped with a
                       note if the file doesn't exist (default: ao3_tag_synonyms.csv)
--literal-out FILE    Literal-vs-wrangled split CSV output
                       (default: ao3_seed_tag_literal_usage.csv)
--breakdown-out FILE  Per-synonym breakdown CSV output
                       (default: ao3_seed_tag_synonym_breakdown.csv)
-h, --help
```

### Notebook

`ao3_tag_wrangling.ipynb` is a Jupyter notebook version of the same tool,
structured like `ao3_tag_fandom_labels.ipynb` — edit the Configuration cell,
then run all cells in order. Both tables render inline in addition to being
saved to disk.

## AO3 terms of service

AO3 asks that scraping tools wait between requests to avoid overloading their
servers. This tool enforces a **minimum 5-second delay** (`REQUEST_DELAY`) between
every request. Please do not reduce this value.

See: [archiveofourown.org/TOS](https://archiveofourown.org/TOS)

## License

CC BY-NC 4.0, matching [mstanfill/AO3MetadataScraper](https://github.com/mstanfill/AO3MetadataScraper).
