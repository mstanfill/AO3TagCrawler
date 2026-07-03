#!/usr/bin/env python3
"""Scrape the AO3 tag list and the most recent works for each tag.

Three-step pipeline, mirroring mstanfill/AO3MetadataScraper's design:

  Step 1 - collect the tag list from https://archiveofourown.org/tags
           (the <ul class="tags cloud index group"> tag cloud) -> tags CSV
  Step 2 - for each tag, page through its works listing and collect up to
           --limit-per-tag most-recent work IDs                -> work-IDs CSV
  Step 3 - fetch each work page and scrape its metadata          -> metadata CSV

Never downloads fic body text. Enforces AO3's requested minimum 5-second
delay between requests and retries transient errors with exponential
backoff.
"""
import argparse
import csv
import re
import sys
import time
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://archiveofourown.org"
TAGS_URL = f"{BASE_URL}/tags"

REQUEST_DELAY = 5  # seconds, minimum delay between requests per AO3 ToS
RETRY_BACKOFFS = [15, 30, 60, 120, 240]  # seconds
RETRYABLE_STATUS = {429, 500, 502, 503, 504, 525}
SKIP_STATUS = {403, 404}

WORK_ID_RE = re.compile(r"^work_(\d+)$")

METADATA_FIELDS = [
    "tag", "work_id", "title", "author", "rating", "warnings", "category",
    "fandom", "relationship", "character", "additional_tags", "language",
    "series", "published", "status", "status_date", "words", "chapters",
    "comments", "kudos", "bookmarks", "hits", "summary",
]


def make_session(user_agent_suffix=None):
    session = requests.Session()
    user_agent = "AO3TagScraper/1.0 (+https://github.com/mstanfill/AO3TagCrawler)"
    if user_agent_suffix:
        user_agent = f"{user_agent} {user_agent_suffix}"
    # Some responses (notably /tags) appear to vary by request headers beyond
    # User-Agent; send a standard browser-like header set so we get the same
    # content a real browser would.
    session.headers.update({
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Connection": "keep-alive",
    })
    return session


def fetch(session, url):
    """GET url with rate-limiting and retry/backoff. Returns Response or None."""
    for attempt in range(len(RETRY_BACKOFFS)):
        time.sleep(REQUEST_DELAY)
        try:
            resp = session.get(url, timeout=60)
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
            print(f"  ! request error on {url}: {exc}", file=sys.stderr)
            if attempt == len(RETRY_BACKOFFS) - 1:
                return None
            time.sleep(RETRY_BACKOFFS[attempt])
            continue

        if resp.status_code == 200:
            return resp
        if resp.status_code in SKIP_STATUS:
            print(f"  ! {resp.status_code} on {url}, skipping", file=sys.stderr)
            return None
        if resp.status_code in RETRYABLE_STATUS:
            print(f"  ! {resp.status_code} on {url}, retrying", file=sys.stderr)
            if attempt == len(RETRY_BACKOFFS) - 1:
                return None
            time.sleep(RETRY_BACKOFFS[attempt])
            continue
        print(f"  ! unexpected status {resp.status_code} on {url}, skipping", file=sys.stderr)
        return None
    return None


# ---------------------------------------------------------------------------
# Step 1: tag list
# ---------------------------------------------------------------------------

def parse_tag_list(html):
    """Extract (tag_name, tag_href) pairs from the /tags page's tag clouds.

    The page has several "<h3 class="landmark heading">...</h3>" sections,
    each followed by its own "<ul class="tags cloud index group">" (e.g. the
    "Browse Popular Tags" summary section, which is often empty, plus one
    per fandom/category with its own populated cloud). Collect from every
    such <ul>, not just the first, and dedupe by tag name.

    Tag-cloud links aren't marked with a stable class like "tag" -- AO3
    sizes them by popularity with classes like "cloud1".."cloudN" (e.g.
    <a class="cloud2" href="/tags/Abduction/works">Abduction</a>), so take
    every <a> inside the <ul> rather than filtering by class.
    """
    soup = BeautifulSoup(html, "html.parser")
    seen = set()
    tags = []
    for container in soup.select("ul.tags.cloud.index.group"):
        for a in container.select("a"):
            name = a.get_text(strip=True)
            href = a.get("href", "")
            if name and href and name not in seen:
                seen.add(name)
                tags.append((name, href))
    return tags


def tag_works_url(href):
    """Resolve a tag's href to its works-listing URL."""
    url = urljoin(BASE_URL + "/", href)
    path = urlparse(url).path.rstrip("/")
    if not path.endswith("/works"):
        path = f"{path}/works"
    return f"{BASE_URL}{path}"


def scrape_tag_list(session, tags_out):
    print(f"Step 1: fetching tag list from {TAGS_URL}")
    resp = fetch(session, TAGS_URL)
    if resp is None:
        print("Failed to fetch tag list.", file=sys.stderr)
        return []

    tags = parse_tag_list(resp.text)
    if not tags:
        debug_file = "debug_tags_page.html"
        with open(debug_file, "w", encoding="utf-8") as debug_f:
            debug_f.write(resp.text)
        print("No tags found in <ul class=\"tags cloud index group\">.", file=sys.stderr)
        print(f"  wrote the raw response to {debug_file} for comparison against "
              f"a browser's View Source", file=sys.stderr)

    with open(tags_out, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["tag_name", "tag_href", "tag_url"])
        rows = []
        for name, href in tags:
            url = tag_works_url(href)
            writer.writerow([name, href, url])
            rows.append((name, url))

    print(f"  found {len(rows)} tags -> {tags_out}")
    return rows


def load_tags(tags_file):
    rows = []
    with open(tags_file, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append((row["tag_name"], row["tag_url"]))
    return rows


# ---------------------------------------------------------------------------
# Step 2: work IDs per tag
# ---------------------------------------------------------------------------

def _ids_from_soup(soup):
    ids = []
    for li in soup.find_all("li", id=WORK_ID_RE):
        match = WORK_ID_RE.match(li["id"])
        if match:
            ids.append(match.group(1))
    return ids


def collect_work_ids_for_tag(session, tag_url, limit):
    seen = set()
    ids = []
    page = 1
    while len(ids) < limit:
        url = f"{tag_url}?page={page}&view_adult=true"
        resp = fetch(session, url)
        if resp is None:
            break
        page_ids = _ids_from_soup(BeautifulSoup(resp.text, "html.parser"))
        if not page_ids:
            break
        added = False
        for work_id in page_ids:
            if work_id not in seen:
                seen.add(work_id)
                ids.append(work_id)
                added = True
                if len(ids) >= limit:
                    break
        if not added:
            break
        page += 1
    return ids[:limit]


def collect_work_ids(session, tags, ids_out, limit):
    print(f"Step 2: collecting up to {limit} recent work IDs per tag")
    with open(ids_out, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["tag_name", "work_id"])
        pairs = []
        for tag_name, tag_url in tags:
            print(f"  [{tag_name}] {tag_url}")
            work_ids = collect_work_ids_for_tag(session, tag_url, limit)
            print(f"    -> {len(work_ids)} work IDs")
            for work_id in work_ids:
                writer.writerow([tag_name, work_id])
                f.flush()
                pairs.append((tag_name, work_id))
    print(f"  wrote {len(pairs)} (tag, work_id) pairs -> {ids_out}")
    return pairs


def load_work_ids(ids_file):
    pairs = []
    with open(ids_file, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            pairs.append((row["tag_name"], row["work_id"]))
    return pairs


# ---------------------------------------------------------------------------
# Step 3: work metadata
# ---------------------------------------------------------------------------

def _tag_list_field(meta, klass):
    dd = meta.find("dd", class_=klass)
    if not dd:
        return ""
    # Don't filter by the anchor's own class (e.g. AO3's tag-cloud links use
    # popularity classes like "cloud2" rather than a stable "tag" class) --
    # the <dd> is already scoped to one field, so any <a> inside it is a tag.
    return ", ".join(a.get_text(strip=True) for a in dd.find_all("a"))


def _simple_field(meta, klass):
    dd = meta.find("dd", class_=klass)
    return dd.get_text(strip=True) if dd else ""


def _stat_field(stats, klass):
    dd = stats.find("dd", class_=klass)
    return dd.get_text(strip=True) if dd else ""


def parse_work_metadata(html, work_id, tag_name):
    soup = BeautifulSoup(html, "html.parser")

    title_el = soup.select_one("h2.title.heading")
    title = title_el.get_text(strip=True) if title_el else ""

    authors = [a.get_text(strip=True) for a in soup.select("h3.byline.heading a[rel=author]")]
    author = ", ".join(authors) if authors else "Anonymous"

    summary_el = soup.select_one("div.summary.module blockquote.userstuff")
    summary = summary_el.get_text(" ", strip=True) if summary_el else ""

    meta = soup.select_one("dl.work.meta.group")
    row = {
        "tag": tag_name,
        "work_id": work_id,
        "title": title,
        "author": author,
        "summary": summary,
    }
    if meta is not None:
        row["rating"] = _tag_list_field(meta, "rating")
        row["warnings"] = _tag_list_field(meta, "warning")
        row["category"] = _tag_list_field(meta, "category")
        row["fandom"] = _tag_list_field(meta, "fandom")
        row["relationship"] = _tag_list_field(meta, "relationship")
        row["character"] = _tag_list_field(meta, "character")
        row["additional_tags"] = _tag_list_field(meta, "freeform")
        row["language"] = _simple_field(meta, "language")
        row["series"] = _simple_field(meta, "series")

        stats = meta.select_one("dl.stats") or meta
        row["words"] = _stat_field(stats, "words")
        row["chapters"] = _stat_field(stats, "chapters")
        row["comments"] = _stat_field(stats, "comments")
        row["kudos"] = _stat_field(stats, "kudos")
        row["bookmarks"] = _stat_field(stats, "bookmarks")
        row["hits"] = _stat_field(stats, "hits")
        row["published"] = _stat_field(stats, "published")

        status_dt = stats.find("dt", class_="status")
        status_dd = stats.find("dd", class_="status")
        row["status"] = status_dt.get_text(strip=True).rstrip(":") if status_dt else "In Progress"
        row["status_date"] = status_dd.get_text(strip=True) if status_dd else ""
    else:
        for field in METADATA_FIELDS:
            row.setdefault(field, "")

    return {field: row.get(field, "") for field in METADATA_FIELDS}


def already_done(out_file):
    done = set()
    try:
        with open(out_file, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                done.add((row["tag"], row["work_id"]))
    except FileNotFoundError:
        pass
    return done


def collect_metadata(session, pairs, out_file, errors_file, resume):
    print(f"Step 3: collecting metadata for {len(pairs)} works")

    skip = already_done(out_file) if resume else set()
    if skip:
        print(f"  resuming: {len(skip)} works already in {out_file}")

    out_mode = "a" if resume and skip else "w"
    err_mode = "a" if resume and skip else "w"

    with open(out_file, out_mode, newline="", encoding="utf-8") as out_f, \
            open(errors_file, err_mode, newline="", encoding="utf-8") as err_f:
        writer = csv.DictWriter(out_f, fieldnames=METADATA_FIELDS)
        err_writer = csv.writer(err_f)
        if out_mode == "w":
            writer.writeheader()
        if err_mode == "w":
            err_writer.writerow(["tag_name", "work_id", "reason"])

        done_count = 0
        for tag_name, work_id in pairs:
            if (tag_name, work_id) in skip:
                continue
            url = f"{BASE_URL}/works/{work_id}?view_adult=true"
            resp = fetch(session, url)
            if resp is None:
                err_writer.writerow([tag_name, work_id, "fetch failed"])
                err_f.flush()
                continue
            row = parse_work_metadata(resp.text, work_id, tag_name)
            writer.writerow(row)
            out_f.flush()
            done_count += 1
            if done_count % 10 == 0:
                print(f"  ... {done_count}/{len(pairs) - len(skip)} new works done")

    print(f"  wrote metadata -> {out_file} (errors, if any, -> {errors_file})")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_arg_parser():
    parser = argparse.ArgumentParser(
        description="Scrape the AO3 tag list, then the most recent works for each tag.",
    )
    parser.add_argument("--limit-per-tag", type=int, default=100,
                         help="Most recent works to collect per tag (default: 100)")
    parser.add_argument("--tags-out", default="ao3_tags.csv",
                         help="Step 1 output CSV (default: ao3_tags.csv)")
    parser.add_argument("--ids-out", default="ao3_tag_work_ids.csv",
                         help="Step 2 output CSV (default: ao3_tag_work_ids.csv)")
    parser.add_argument("--out", default="ao3_tag_metadata.csv",
                         help="Step 3 metadata output CSV (default: ao3_tag_metadata.csv)")
    parser.add_argument("--errors-out", default=None,
                         help="Step 3 errors CSV (default: errors_<--out>)")
    parser.add_argument("--tags-only", action="store_true",
                         help="Run Step 1 only, then stop")
    parser.add_argument("--step2", metavar="TAGS_FILE",
                         help="Skip Step 1; load tags from an existing tags CSV")
    parser.add_argument("--step3", metavar="IDS_FILE",
                         help="Skip Steps 1-2; load (tag, work_id) pairs from an existing CSV")
    parser.add_argument("--resume", action="store_true",
                         help="Step 3: skip (tag, work_id) pairs already present in --out")
    parser.add_argument("--header", metavar="AGENT", default=None,
                         help='User-Agent suffix, e.g. "MyProject/1.0; me@email.com"')
    return parser


def main(argv=None):
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    errors_out = args.errors_out or f"errors_{args.out}"

    session = make_session(args.header)

    if args.step3:
        pairs = load_work_ids(args.step3)
        collect_metadata(session, pairs, args.out, errors_out, args.resume)
        return

    if args.step2:
        tags = load_tags(args.step2)
    else:
        tags = scrape_tag_list(session, args.tags_out)
        if args.tags_only:
            return

    if not tags:
        print("No tags to process, stopping.", file=sys.stderr)
        return

    pairs = collect_work_ids(session, tags, args.ids_out, args.limit_per_tag)
    if not pairs:
        print("No work IDs collected, stopping.", file=sys.stderr)
        return

    collect_metadata(session, pairs, args.out, errors_out, args.resume)


if __name__ == "__main__":
    main()
