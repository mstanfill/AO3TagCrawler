#!/usr/bin/env python3
"""Exercises ao3_tag_fandom_spread.py against a synthetic metadata CSV with
hand-countable fandom spread per tag.

No network access needed -- this only reads/writes local files. Run with:
    python tests/test_ao3_tag_fandom_spread.py
"""
import csv
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ao3_tag_visualizer as viz
import ao3_tag_fandom_spread as fs

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


def base_row(work_id, tag, fandom, additional_tags):
    row = {field: "" for field in METADATA_FIELDS}
    row.update({
        "tag": tag, "work_id": work_id, "title": f"Work {work_id}", "author": "author",
        "rating": "Teen And Up Audiences", "warnings": "No Archive Warnings Apply",
        "category": "Gen", "fandom": fandom, "additional_tags": additional_tags,
        "language": "English", "published": "2026-01-01", "status": "Completed",
        "status_date": "2026-01-01", "words": "1000", "chapters": "1/1",
        "comments": "0", "kudos": "0", "bookmarks": "0", "hits": "0",
    })
    return row


# ---------------------------------------------------------------------------
# Fixture (distinct-work co-occurrence, hand-counted):
#   Angst -> works 1 (Alpha), 2 (Beta), 3 (Gamma)      -> n_works 3, n_fandoms 3
#   Fluff -> works 1 (Alpha), 4 (Alpha, Beta crossover)-> n_works 2, n_fandoms 2
#            Alpha in works 1 & 4 = 100%, Beta in work 4 = 50%.
#   work 1 is ALSO scraped under a second seed tag (S2) with identical
#     metadata -- the work_id dedup must keep it counted once, not twice.
#   work 5 has NO additional_tags -> contributes no additional_tags row.
# ---------------------------------------------------------------------------

def build_fixture_rows():
    return [
        base_row(1, "S1", "Alpha", "Angst, Fluff"),
        base_row(1, "S2", "Alpha", "Angst, Fluff"),   # same work, 2nd seed tag
        base_row(2, "S1", "Beta", "Angst"),
        base_row(3, "S1", "Gamma", "Angst"),
        base_row(4, "S1", "Alpha, Beta", "Fluff"),     # crossover work
        base_row(5, "S1", "Alpha", ""),                # no additional_tags
    ]


def write_fixture_csv(path):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=METADATA_FIELDS)
        writer.writeheader()
        writer.writerows(build_fixture_rows())


def run_spread_checks(tmpdir):
    csv_path = os.path.join(tmpdir, "metadata.csv")
    write_fixture_csv(csv_path)
    df = viz.load_metadata(csv_path)

    spread = fs.fandom_spread(df, field="additional_tags", top_n=3)
    check("columns are [field, value, n_works, n_fandoms, top_fandoms]",
          list(spread.columns) == fs.OUT_COLUMNS, f"got {list(spread.columns)}")
    check("one row per distinct additional_tags value (Angst, Fluff)",
          set(spread["value"]) == {"Angst", "Fluff"}, f"got {spread['value'].tolist()}")

    by_value = spread.set_index("value")
    check("Angst spans 3 distinct fandoms (Alpha/Beta/Gamma)",
          by_value.loc["Angst", "n_fandoms"] == 3, f"got {by_value.loc['Angst', 'n_fandoms']}")
    check("Angst is on 3 distinct works -- the multi-seed-tag duplicate of work 1 "
          "is not double-counted",
          by_value.loc["Angst", "n_works"] == 3, f"got {by_value.loc['Angst', 'n_works']}")
    check("Fluff spans 2 fandoms, on 2 works (crossover work 4 counts both fandoms)",
          by_value.loc["Fluff", "n_fandoms"] == 2 and by_value.loc["Fluff", "n_works"] == 2,
          f"got {by_value.loc['Fluff', ['n_fandoms', 'n_works']].to_dict()}")
    check("Fluff top_fandoms uses the tag's own work count as the denominator "
          "(Alpha 100%, Beta 50%)",
          by_value.loc["Fluff", "top_fandoms"] == "Alpha (100%), Beta (50%)",
          f"got {by_value.loc['Fluff', 'top_fandoms']!r}")

    check("rows are sorted most-cross-cutting first (Angst before Fluff)",
          spread["value"].tolist() == ["Angst", "Fluff"], f"got {spread['value'].tolist()}")

    # A different field works too (fandom-vs-itself is ~100% self, n_fandoms>=1).
    fandom_spread = fs.fandom_spread(df, field="fandom", top_n=3)
    check("--field fandom analyzes fandom values instead",
          set(fandom_spread["value"]) == {"Alpha", "Beta", "Gamma"},
          f"got {fandom_spread['value'].tolist()}")


def run_cli_checks(tmpdir, script_path):
    parser = fs.build_arg_parser()
    default_args = parser.parse_args([])
    check("--input defaults to ao3_tag_metadata.csv",
          default_args.input == "ao3_tag_metadata.csv")
    check("--field defaults to additional_tags", default_args.field == "additional_tags")
    check("--top-n defaults to 3", default_args.top_n == 3)
    check("--out defaults to ao3_tag_fandom_spread.csv",
          default_args.out == "ao3_tag_fandom_spread.csv")

    csv_path = os.path.join(tmpdir, "cli_metadata.csv")
    write_fixture_csv(csv_path)
    out_path = os.path.join(tmpdir, "spread.csv")
    result = subprocess.run(
        [sys.executable, script_path, "--input", csv_path, "--out", out_path,
         "--field", "additional_tags", "--top-n", "2"],
        capture_output=True, text=True,
    )
    check("main() exits 0", result.returncode == 0, f"stderr: {result.stderr}")
    check("main() writes the spread CSV", os.path.exists(out_path))
    with open(out_path, newline="", encoding="utf-8") as f:
        out_rows = list(csv.DictReader(f))
    check("spread CSV columns match OUT_COLUMNS",
          list(out_rows[0].keys()) == fs.OUT_COLUMNS, f"got {list(out_rows[0].keys())}")
    check("spread CSV first row is the most cross-cutting tag (Angst, n_fandoms 3)",
          out_rows[0]["value"] == "Angst" and out_rows[0]["n_fandoms"] == "3",
          f"got {out_rows[0]}")


def main():
    tmpdir = tempfile.mkdtemp(prefix="ao3_spread_test_")
    script_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "ao3_tag_fandom_spread.py")
    run_spread_checks(tmpdir)
    run_cli_checks(tmpdir, script_path)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
        sys.exit(1)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
