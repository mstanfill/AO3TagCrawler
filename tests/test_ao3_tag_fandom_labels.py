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

    # Regression: the scraper emits one row per (seed tag, work), so a work
    # found via 3 seed tags appears 3 times in the metadata CSV. An earlier
    # version exploded the fandom column from the raw (non-deduped) df while
    # the denominator counted each work once, inflating this exact shape to
    # "Fandom A (300%)" on real data.
    dup_rows = [
        base_row(9001, "Fandom A", character="DupBob", tag="Seed1"),
        base_row(9001, "Fandom A", character="DupBob", tag="Seed2"),
        base_row(9001, "Fandom A", character="DupBob", tag="Seed3"),
        base_row(9002, "Fandom B", character="DupBob", tag="Seed1"),
    ]
    dup_df = viz.pd.DataFrame(dup_rows).astype(str)
    dup_labels = labels_mod.compute_fandom_labels(dup_df, {"character::DupBob"}, top_n=3)
    check("a work duplicated across 3 seed tags counts once, not three times",
          dup_labels["character::DupBob"] == "Fandom A (50%), Fandom B (50%)",
          f"got {dup_labels['character::DupBob']!r}")

    # A genuine crossover work (two fandoms in one cell): the SUM may exceed
    # 100 by design (the work genuinely belongs to both fandoms), but every
    # individual percentage must stay <= 100.
    crossover_rows = [
        base_row(9101, "Fandom X, Fandom Y", character="XoverCarol"),
        base_row(9102, "Fandom X", character="XoverCarol"),
    ]
    crossover_df = viz.pd.DataFrame(crossover_rows).astype(str)
    crossover_labels = labels_mod.compute_fandom_labels(
        crossover_df, {"character::XoverCarol"}, top_n=3)
    check("a genuine crossover work counts toward each of its fandoms, individual values <= 100",
          crossover_labels["character::XoverCarol"] == "Fandom X (100%), Fandom Y (50%)",
          f"got {crossover_labels['character::XoverCarol']!r}")

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
    check("--cluster-fandoms-out defaults to ao3_cluster_fandoms.csv",
          default_args.cluster_fandoms_out == "ao3_cluster_fandoms.csv")

    # CLI: end-to-end run.
    clusters_csv_path = os.path.join(tmpdir, "clusters.csv")
    write_clusters_csv(clusters_csv_path, sorted(tag_ids))
    out_path = os.path.join(tmpdir, "labeled.csv")
    cluster_fandoms_path = os.path.join(tmpdir, "cluster_fandoms.csv")
    result = subprocess.run(
        [sys.executable, script_path, "--input", csv_path, "--clusters-csv", clusters_csv_path,
         "--top-n", "2", "--out", out_path,
         "--cluster-fandoms-out", cluster_fandoms_path],
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

    check("CLI run writes the per-cluster fandom summary CSV",
          os.path.exists(cluster_fandoms_path))
    with open(cluster_fandoms_path, newline="", encoding="utf-8") as f:
        summary_rows = list(csv.DictReader(f))
    check("cluster summary CSV has [cluster_id, n_tags, n_works, top_fandoms] columns",
          summary_rows and list(summary_rows[0].keys()) == ["cluster_id", "n_tags", "n_works", "top_fandoms"],
          f"got {list(summary_rows[0].keys()) if summary_rows else summary_rows}")
    check("cluster summary CSV has one row per cluster in the input",
          len(summary_rows) == 5, f"got {len(summary_rows)} rows")

    # --column-name.
    out_path2 = os.path.join(tmpdir, "labeled2.csv")
    result2 = subprocess.run(
        [sys.executable, script_path, "--input", csv_path, "--clusters-csv", clusters_csv_path,
         "--column-name", "fandom_guess", "--out", out_path2,
         "--cluster-fandoms-out", os.path.join(tmpdir, "cluster_fandoms2.csv")],
        capture_output=True, text=True,
    )
    check("main() with --column-name exits 0", result2.returncode == 0, f"stderr: {result2.stderr}")
    with open(out_path2, newline="", encoding="utf-8") as f:
        header = next(csv.reader(f))
    check("--column-name renames the new column", "fandom_guess" in header, f"got {header}")

    # A clusters CSV without a cluster_id column: per-tag labeling still
    # runs, the per-cluster summary is skipped with a note, exit code 0.
    no_cluster_csv = os.path.join(tmpdir, "no_cluster_col.csv")
    with open(no_cluster_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["tag_id"])
        writer.writerow(["character::Bob"])
    no_cluster_summary_path = os.path.join(tmpdir, "no_cluster_summary.csv")
    result3 = subprocess.run(
        [sys.executable, script_path, "--input", csv_path, "--clusters-csv", no_cluster_csv,
         "--out", os.path.join(tmpdir, "no_cluster_labeled.csv"),
         "--cluster-fandoms-out", no_cluster_summary_path],
        capture_output=True, text=True,
    )
    check("a clusters CSV without cluster_id still exits 0",
          result3.returncode == 0, f"stderr: {result3.stderr}")
    check("missing cluster_id column skips the summary with a note",
          "no cluster_id column" in result3.stderr and not os.path.exists(no_cluster_summary_path),
          f"stderr: {result3.stderr}")


def run_cluster_summary_checks():
    # Direct unit test of compute_cluster_fandom_summary against grouped
    # clusters (several tags sharing one cluster_id), which the CLI fixture
    # above doesn't exercise (write_clusters_csv gives each tag its own
    # cluster).
    rows = []
    # Cluster 1 = {Bob, Angst}: works 1-3 carry BOTH tags (Fandom A), work
    # 4 only Angst (Fandom B) -- the cluster's work pool is 4 distinct
    # stories, not 7 tag-occurrences.
    for wid in (1, 2, 3):
        rows.append(base_row(wid, "Fandom A", character="Bob", additional_tags="Angst"))
    rows.append(base_row(4, "Fandom B", additional_tags="Angst"))
    # Cluster 2 = {Crossover_Trope}: 6 works across three fandoms (3/2/1).
    for wid, fandom in [(5, "Fandom X"), (6, "Fandom X"), (7, "Fandom X"),
                         (8, "Fandom Y"), (9, "Fandom Y"), (10, "Fandom Z")]:
        rows.append(base_row(wid, fandom, additional_tags="Crossover_Trope"))
    df = viz.pd.DataFrame(rows).astype(str)

    clusters_df = viz.pd.DataFrame([
        {"tag_id": "character::Bob", "cluster_id": "1"},
        {"tag_id": "additional_tags::Angst", "cluster_id": "1"},
        {"tag_id": "additional_tags::Crossover_Trope", "cluster_id": "2"},
        {"tag_id": "additional_tags::Nonexistent", "cluster_id": "3"},
        {"tag_id": "additional_tags::AlsoNonexistent", "cluster_id": "10"},
    ])

    summary = labels_mod.compute_cluster_fandom_summary(df, clusters_df, top_n=2)
    by_cluster = {row["cluster_id"]: row for _, row in summary.iterrows()}

    check("a work with several of the cluster's tags counts once (4 works, not 7)",
          by_cluster["1"]["n_works"] == 4, f"got {by_cluster['1'].to_dict()}")
    check("cluster-level fandom percentages use the cluster's whole work pool",
          by_cluster["1"]["top_fandoms"] == "Fandom A (75%), Fandom B (25%)",
          f"got {by_cluster['1']['top_fandoms']!r}")
    check("cluster summary respects top_n truncation",
          by_cluster["2"]["top_fandoms"] == "Fandom X (50%), Fandom Y (33%)",
          f"got {by_cluster['2']['top_fandoms']!r}")
    check("n_tags counts the cluster's distinct tags",
          by_cluster["1"]["n_tags"] == 2 and by_cluster["2"]["n_tags"] == 1,
          f"got {summary.to_dict('records')}")
    check("a cluster whose tags never appear in the metadata keeps its row "
          "with n_works=0 and an empty label",
          by_cluster["3"]["n_works"] == 0 and by_cluster["3"]["top_fandoms"] == "",
          f"got {by_cluster['3'].to_dict()}")
    check("clusters are ordered numerically (2 before 10), not lexicographically",
          summary["cluster_id"].tolist() == ["1", "2", "3", "10"],
          f"got {summary['cluster_id'].tolist()}")


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
    run_cluster_summary_checks()
    run_large_scale_checks()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
        sys.exit(1)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
