#!/usr/bin/env python3
"""Exercises ao3_tag_counts.py against a synthetic metadata CSV with
hand-countable tags per work.

No network access needed -- this only reads/writes local files. Run with:
    python tests/test_ao3_tag_counts.py
"""
import csv
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ao3_tag_visualizer as viz
import ao3_tag_analysis as analysis
import ao3_tag_counts as counts

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


def base_row(work_id, tag, rating="Teen And Up Audiences",
             warnings="No Archive Warnings Apply", category="Gen", fandom="Fandom_A",
             relationship="", character="", additional_tags=""):
    row = {field: "" for field in METADATA_FIELDS}
    row.update({
        "tag": tag, "work_id": work_id, "title": f"Work {work_id}", "author": "author",
        "rating": rating, "warnings": warnings, "category": category, "fandom": fandom,
        "relationship": relationship, "character": character,
        "additional_tags": additional_tags, "language": "English",
        "published": "2026-01-01", "status": "Completed", "status_date": "2026-01-01",
        "words": "1000", "chapters": "1/1", "comments": "0", "kudos": "0",
        "bookmarks": "0", "hits": "0",
    })
    return row


# ---------------------------------------------------------------------------
# Fixture: three works with hand-countable tag totals.
#
#   work 1 (under TWO seed tags -- must dedupe to one work):
#     rating(1) + warnings(1) + category(1) + fandom "A, B"(2) + character
#     "X"(1) + additional_tags "p, q, r"(3), relationship blank(0) = 9 total;
#     additional_tags = 3.
#   work 2: additional_tags "Angst, Angst" -- within-cell duplicate counts
#     once(1); rating(1)+warnings(1)+category(1)+fandom "Fandom_A"(1)+addl(1)
#     = 5 total; additional_tags = 1.
#   work 3: ZERO additional_tags; rating+warnings+category+fandom = 4 total;
#     additional_tags = 0 (must still count as a story -> zeros included).
#
# all_fields totals: 9, 5, 4 -> n_works=3, total=18, mean=6.0, min=4,
#   median=5, max=9.
# additional_tags: 3, 1, 0 -> total=4, mean~1.33, min=0, max=3.
# fandom: 2, 1, 1 -> total=4, max=2.
# ---------------------------------------------------------------------------

def build_fixture_rows():
    return [
        base_row(1, "Seed1", fandom="A, B", character="X", additional_tags="p, q, r"),
        base_row(1, "Seed2", fandom="A, B", character="X", additional_tags="p, q, r"),
        base_row(2, "Seed1", additional_tags="Angst, Angst"),
        base_row(3, "Seed1"),
    ]


def write_fixture_csv(path):
    rows = build_fixture_rows()
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=METADATA_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return rows


def run_stats_checks(tmpdir):
    csv_path = os.path.join(tmpdir, "metadata.csv")
    write_fixture_csv(csv_path)
    df = viz.load_metadata(csv_path)

    stats = counts.tags_per_story_stats(df)
    check("stats has an all_fields row plus one per metadata field (8 rows)",
          stats["scope"].tolist() == ["all_fields"] + analysis.ALL_METADATA_FIELDS,
          f"got {stats['scope'].tolist()}")
    by_scope = stats.set_index("scope")

    check("n_works dedupes the scraper's one-row-per-(seed tag, work) shape (3 works)",
          by_scope.loc["all_fields", "n_works"] == 3,
          f"got {by_scope.loc['all_fields', 'n_works']}")
    check("all_fields total_tags is 9+5+4 = 18",
          by_scope.loc["all_fields", "total_tags"] == 18,
          f"got {by_scope.loc['all_fields', 'total_tags']}")
    check("all_fields mean is 6.0", by_scope.loc["all_fields", "mean"] == 6.0,
          f"got {by_scope.loc['all_fields', 'mean']}")
    check("all_fields min/median/max are 4/5/9",
          (by_scope.loc["all_fields", "min"], by_scope.loc["all_fields", "median"],
           by_scope.loc["all_fields", "max"]) == (4, 5.0, 9),
          f"got {by_scope.loc['all_fields', ['min', 'median', 'max']].to_dict()}")

    check("a within-cell duplicate (Angst, Angst) counts once",
          by_scope.loc["additional_tags", "total_tags"] == 4,
          f"got {by_scope.loc['additional_tags', 'total_tags']}")
    check("zeros are included: the work with no additional_tags makes min=0",
          by_scope.loc["additional_tags", "min"] == 0,
          f"got {by_scope.loc['additional_tags', 'min']}")
    check("additional_tags mean is over every story (4/3 = 1.33), not just taggers",
          by_scope.loc["additional_tags", "mean"] == 1.33,
          f"got {by_scope.loc['additional_tags', 'mean']}")
    check("additional_tags max is 3", by_scope.loc["additional_tags", "max"] == 3,
          f"got {by_scope.loc['additional_tags', 'max']}")
    check("a multi-valued fandom cell (A, B) counts 2 for that work",
          by_scope.loc["fandom", "max"] == 2 and by_scope.loc["fandom", "total_tags"] == 4,
          f"got {by_scope.loc['fandom', ['max', 'total_tags']].to_dict()}")
    check("a single-work-only field (character) still spans every story (min 0)",
          by_scope.loc["character", "min"] == 0 and by_scope.loc["character", "n_works"] == 3,
          f"got {by_scope.loc['character', ['min', 'n_works']].to_dict()}")


def run_cli_checks(tmpdir, script_path):
    parser = counts.build_arg_parser()
    default_args = parser.parse_args([])
    check("--input defaults to ao3_tag_metadata.csv",
          default_args.input == "ao3_tag_metadata.csv")
    check("--out defaults to ao3_tags_per_story_stats.csv",
          default_args.out == "ao3_tags_per_story_stats.csv")

    csv_path = os.path.join(tmpdir, "cli_metadata.csv")
    write_fixture_csv(csv_path)
    out_path = os.path.join(tmpdir, "stats.csv")
    result = subprocess.run(
        [sys.executable, script_path, "--input", csv_path, "--out", out_path],
        capture_output=True, text=True,
    )
    check("main() exits 0", result.returncode == 0, f"stderr: {result.stderr}")
    check("main() writes the stats CSV", os.path.exists(out_path))
    with open(out_path, newline="", encoding="utf-8") as f:
        out_rows = {row["scope"]: row for row in csv.DictReader(f)}
    check("stats CSV columns are the describe()-style summary",
          list(next(iter(out_rows.values())).keys()) == counts.STAT_COLUMNS,
          f"got {list(next(iter(out_rows.values())).keys())}")
    check("stats CSV matches the direct function call (all_fields mean 6.0)",
          out_rows["all_fields"]["mean"] == "6.0", f"got {out_rows['all_fields']['mean']!r}")
    check("stats CSV has all_fields + one row per field (8 rows)",
          len(out_rows) == 8, f"got {len(out_rows)} rows")


def main():
    tmpdir = tempfile.mkdtemp(prefix="ao3_counts_test_")
    script_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "ao3_tag_counts.py")

    run_stats_checks(tmpdir)
    run_cli_checks(tmpdir, script_path)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
        sys.exit(1)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
