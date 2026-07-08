#!/usr/bin/env python3
"""Exercises ao3_tag_fandom_labels.py end-to-end against synthetic metadata
and clusters CSVs.

No network access needed -- this only reads/writes local files. Run with:
    python tests/test_ao3_tag_fandom_labels.py
"""
import csv
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ao3_tag_visualizer as viz
import ao3_tag_fandom_labels as labels_mod

METADATA_FIELDS = [
    "tag", "work_id", "title", "author", "rating", "warnings", "category",
    "fandom", "relationship", "character", "additional_tags", "language",
    "series", "published", "status", "status_date", "words", "chapters",
    "comments", "kudos", "bookmarks", "hits", "summary",
]

FAILURES = []


def check(name, condition, detail=""):
    if condition:
        print(f"PASS: {name}")
    else:
        print(f"FAIL: {name} {detail}")
        FAILURES.append(name)


def base_row(work_id, fandom, character="", additional_tags="", tag="Search_Tag"):
    row = {field: "" for field in METADATA_FIELDS}
    row.update({
        "tag": tag,
        "work_id": work_id,
        "title": f"Work {work_id}",
        "author": "author",
        "rating": "Teen And Up Audiences",
        "warnings": "No Archive Warnings Apply",
        "category": "Gen",
        "fandom": fandom,
        "relationship": "",
        "character": character,
        "additional_tags": additional_tags,
        "language": "English",
        "series": "",
        "published": "2026-01-01",
        "status": "Completed",
        "status_date": "2026-01-01",
        "words": "1000",
        "chapters": "1/1",
        "comments": "0",
        "kudos": "0",
        "bookmarks": "0",
        "hits": "0",
        "summary": "",
    })
    return row


# ---------------------------------------------------------------------------
# Fixture: two fandoms.
#   character::Bob only ever appears in Fandom A (3 works) -> should label
#     as "Fandom A (100%)".
#   additional_tags::Angst spans both: 3 works in Fandom A, 1 in Fandom B
#     (4 total) -> "Fandom A (75%), Fandom B (25%)".
#   additional_tags::Crossover_Trope appears in three fandoms with distinct
#     counts (3/2/1) -> exercises --top-n truncation.
#   fandom::Fandom A itself -> should trivially label as "Fandom A (100%)",
#     no special-casing needed (every work tagged fandom::Fandom A has
#     fandom == "Fandom A" by construction).
# ---------------------------------------------------------------------------

def build_fixture_rows():
    rows = []
    work_id = 1

    for _ in range(3):
        rows.append(base_row(work_id, "Fandom A", character="Bob", additional_tags="Angst"))
        work_id += 1
    rows.append(base_row(work_id, "Fandom B", additional_tags="Angst"))
    work_id += 1

    for _ in range(3):
        rows.append(base_row(work_id, "Fandom X", additional_tags="Crossover_Trope"))
        work_id += 1
    for _ in range(2):
        rows.append(base_row(work_id, "Fandom Y", additional_tags="Crossover_Trope"))
        work_id += 1
    rows.append(base_row(work_id, "Fandom Z", additional_tags="Crossover_Trope"))
    work_id += 1

    return rows


def write_fixture_csv(path):
    rows = build_fixture_rows()
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=METADATA_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return rows


def write_clusters_csv(path, tag_ids):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["tag_id", "field", "label", "cluster_id"])
        for i, tag_id in enumerate(tag_ids, start=1):
            field, _, label = tag_id.partition("::")
            writer.writerow([tag_id, field, label, i])


def run_fandom_label_checks(tmpdir, script_path):
    csv_path = os.path.join(tmpdir, "metadata.csv")
    write_fixture_csv(csv_path)
    df = viz.load_metadata(csv_path)

    tag_ids = {
        "character::Bob", "additional_tags::Angst", "additional_tags::Crossover_Trope",
        "fandom::Fandom A", "additional_tags::Nonexistent",
    }

    labels = labels_mod.compute_fandom_labels(df, tag_ids, top_n=3)

    check("single-fandom tag labels as 100% that fandom",
          labels["character::Bob"] == "Fandom A (100%)", f"got {labels['character::Bob']!r}")
    check("multi-fandom tag labels with correct percentages, descending, alphabetical tie-break",
          labels["additional_tags::Angst"] == "Fandom A (75%), Fandom B (25%)",
          f"got {labels['additional_tags::Angst']!r}")
    check("three-fandom tag with --top-n 3 (default) shows all three, descending by count",
          labels["additional_tags::Crossover_Trope"] == "Fandom X (50%), Fandom Y (33%), Fandom Z (17%)",
          f"got {labels['additional_tags::Crossover_Trope']!r}")
    check("a fandom-field tag itself trivially labels as its own fandom at 100%, no special-casing",
          labels["fandom::Fandom A"] == "Fandom A (100%)", f"got {labels['fandom::Fandom A']!r}")
    check("a tag_id absent from the metadata gets an empty label instead of raising",
          labels["additional_tags::Nonexistent"] == "", f"got {labels['additional_tags::Nonexistent']!r}")

    # --top-n truncation.
    labels_top2 = labels_mod.compute_fandom_labels(df, tag_ids, top_n=2)
    check("--top-n 2 truncates the three-fandom tag to its top 2 by count",
          labels_top2["additional_tags::Crossover_Trope"] == "Fandom X (50%), Fandom Y (33%)",
          f"got {labels_top2['additional_tags::Crossover_Trope']!r}")

    # CLI: build_arg_parser defaults.
    parser = labels_mod.build_arg_parser()
    default_args = parser.parse_args([])
    check("--input defaults to ao3_tag_metadata.csv", default_args.input == "ao3_tag_metadata.csv")
    check("--clusters-csv defaults to ao3_tag_clusters.csv",
          default_args.clusters_csv == "ao3_tag_clusters.csv")
    check("--top-n defaults to 3", default_args.top_n == 3)
    check("--column-name defaults to top_fandoms", default_args.column_name == "top_fandoms")
    check("--out defaults to ao3_tag_clusters_with_fandoms.csv",
          default_args.out == "ao3_tag_clusters_with_fandoms.csv")

    # CLI: end-to-end run.
    clusters_csv_path = os.path.join(tmpdir, "clusters.csv")
    write_clusters_csv(clusters_csv_path, sorted(tag_ids))
    out_path = os.path.join(tmpdir, "labeled.csv")
    result = subprocess.run(
        [sys.executable, script_path, "--input", csv_path, "--clusters-csv", clusters_csv_path,
         "--top-n", "2", "--out", out_path],
        capture_output=True, text=True,
    )
    check("main() exits 0", result.returncode == 0, f"stderr: {result.stderr}")
    check("stderr warns about the one stale/unmatched tag_id",
          "1 of 5" in result.stderr, f"stderr: {result.stderr}")
    check("original --clusters-csv is left untouched (a new --out file is written instead)",
          os.path.getsize(clusters_csv_path) > 0
          and "top_fandoms" not in open(clusters_csv_path, encoding="utf-8").read())

    with open(out_path, newline="", encoding="utf-8") as f:
        out_rows = {row["tag_id"]: row for row in csv.DictReader(f)}
    check("labeled CSV keeps every original column plus top_fandoms",
          set(out_rows["character::Bob"].keys()) == {"tag_id", "field", "label", "cluster_id", "top_fandoms"},
          f"got columns {list(out_rows['character::Bob'].keys())}")
    check("labeled CSV's top_fandoms column matches the direct function call (with --top-n 2)",
          out_rows["additional_tags::Crossover_Trope"]["top_fandoms"] == "Fandom X (50%), Fandom Y (33%)",
          f"got {out_rows['additional_tags::Crossover_Trope']['top_fandoms']!r}")

    # --column-name.
    out_path2 = os.path.join(tmpdir, "labeled2.csv")
    result2 = subprocess.run(
        [sys.executable, script_path, "--input", csv_path, "--clusters-csv", clusters_csv_path,
         "--column-name", "fandom_guess", "--out", out_path2],
        capture_output=True, text=True,
    )
    check("main() with --column-name exits 0", result2.returncode == 0, f"stderr: {result2.stderr}")
    with open(out_path2, newline="", encoding="utf-8") as f:
        header = next(csv.reader(f))
    check("--column-name renames the new column", "fandom_guess" in header, f"got {header}")


def run_large_scale_checks():
    # Regression test: an earlier implementation ranked/truncated each
    # tag's fandom list with a Python-level loop over every tag_id's
    # groupby group (assign/sort_values/head/itertuples per group), which
    # paid pandas' per-call overhead once per tag and took 32.1s at 26,427
    # tags -- confirmed directly. The fully-vectorized version (sort_values
    # + groupby().head() over the whole frame at once) produces identical
    # output in under a second at the same scale.
    import random
    import time

    rng = random.Random(0)
    n_works = 20_000
    n_fandoms = 500
    fandoms = [f"Fandom{i}" for i in range(n_fandoms)]

    rows = []
    for work_id in range(n_works):
        fandom = rng.choice(fandoms)
        n_tags = rng.randint(1, 5)
        additional = ", ".join(f"Tag{rng.randint(0, 30_000)}" for _ in range(n_tags))
        rows.append(base_row(work_id, fandom, additional_tags=additional))

    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False,
                                      newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=METADATA_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
        csv_path = f.name

    try:
        df = viz.load_metadata(csv_path)
        tag_table = viz.build_document_tag_table(df, fields=["additional_tags"])
        tag_ids = set(tag_table["tag_id"].unique()) | {f"fandom::{fandom}" for fandom in fandoms}

        start = time.time()
        labels = labels_mod.compute_fandom_labels(df, tag_ids, top_n=3)
        elapsed = time.time() - start
        check(f"large-scale ({len(tag_ids)} tags) fandom labeling completes in under 10s",
              elapsed < 10, f"took {elapsed:.2f}s")
        check("large-scale run labels every requested tag_id",
              set(labels.keys()) == tag_ids, f"got {len(labels)} of {len(tag_ids)}")
    finally:
        os.remove(csv_path)


def main():
    tmpdir = tempfile.mkdtemp(prefix="ao3_fandom_labels_test_")
    script_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "ao3_tag_fandom_labels.py")

    run_fandom_label_checks(tmpdir, script_path)
    run_large_scale_checks()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
        sys.exit(1)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
