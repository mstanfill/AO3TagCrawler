#!/usr/bin/env python3
"""Exercises ao3_tag_wrangling.py end-to-end against synthetic metadata and
relations CSVs.

No network access needed -- this only reads/writes local files. Run with:
    python tests/test_ao3_tag_wrangling.py
"""
import csv
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ao3_tag_visualizer as viz
import ao3_tag_wrangling as wrangling

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


def base_row(work_id, tag, fandom="Some Fandom", relationship="", character="",
             additional_tags=""):
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
        "relationship": relationship,
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
# Fixture: two seed tags.
#
# Seed tag "Angst" (4 works):
#   work 1: additional_tags contains literal "Angst"          -> literal
#   work 2: additional_tags contains case variant "angst"     -> literal
#     (AO3 tags are case-insensitively unique -- same tag, not a synonym)
#   work 3: additional_tags contains "Heavy Angst"            -> synonym
#     (per the hand-built relations CSV)
#   work 4: additional_tags contains only unrelated tags      -> unidentified
#   -> 4 works, 2 literal, literal_pct 50.0, wrangled_pct 50.0
#
# Seed tag "Bob/Carol" (2 works) -- exercises the all-seven-fields literal
# check: the seed tag appears in the RELATIONSHIP field, not
# additional_tags:
#   work 5: relationship "Bob/Carol"                          -> literal
#   work 6: relationship "Carol/Bob" (different string)       -> unidentified
#   -> 2 works, 1 literal, literal_pct 50.0
# ---------------------------------------------------------------------------

def build_fixture_rows():
    return [
        base_row(1, "Angst", additional_tags="Angst, Fluff"),
        base_row(2, "Angst", additional_tags="angst, Slow Burn"),
        base_row(3, "Angst", additional_tags="Heavy Angst"),
        base_row(4, "Angst", additional_tags="Coffee Shop AU"),
        base_row(5, "Bob/Carol", relationship="Bob/Carol"),
        base_row(6, "Bob/Carol", relationship="Carol/Bob"),
    ]


def write_fixture_csv(path):
    rows = build_fixture_rows()
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=METADATA_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return rows


def write_relations_csv(path):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["seed_tag", "relation", "related_tag"])
        writer.writerow(["Angst", "synonym", "Heavy Angst"])
        # A metatag row must be ignored (only synonym/subtag participate).
        writer.writerow(["Angst", "metatag", "Emotional Hurt"])


def run_literal_usage_checks(tmpdir):
    csv_path = os.path.join(tmpdir, "metadata.csv")
    write_fixture_csv(csv_path)
    df = viz.load_metadata(csv_path)

    usage = wrangling.seed_tag_literal_usage(df)
    check("literal usage has one row per seed tag",
          usage["seed_tag"].tolist() == ["Angst", "Bob/Carol"],
          f"got {usage['seed_tag'].tolist()}")

    angst = usage[usage["seed_tag"] == "Angst"].iloc[0]
    check("Angst counts 4 works", angst["n_works"] == 4, f"got {angst.to_dict()}")
    check("Angst counts 2 literal works (one via case-insensitive match)",
          angst["literal_works"] == 2, f"got {angst.to_dict()}")
    check("Angst literal_pct is 50.0", angst["literal_pct"] == 50.0, f"got {angst.to_dict()}")
    check("Angst wrangled_pct is 50.0 (strict two-way partition)",
          angst["wrangled_pct"] == 50.0, f"got {angst.to_dict()}")

    relationship_tag = usage[usage["seed_tag"] == "Bob/Carol"].iloc[0]
    check("a seed tag appearing in the relationship field counts as literal "
          "(all seven fields are checked, not just additional_tags)",
          relationship_tag["literal_works"] == 1 and relationship_tag["n_works"] == 2,
          f"got {relationship_tag.to_dict()}")

    check("rows are sorted by n_works descending",
          usage["n_works"].tolist() == sorted(usage["n_works"].tolist(), reverse=True),
          f"got {usage['n_works'].tolist()}")


def run_breakdown_checks(tmpdir):
    csv_path = os.path.join(tmpdir, "metadata2.csv")
    write_fixture_csv(csv_path)
    relations_path = os.path.join(tmpdir, "relations.csv")
    write_relations_csv(relations_path)

    df = viz.load_metadata(csv_path)
    relations_df = wrangling.pd.read_csv(relations_path, dtype=str, keep_default_na=False)
    breakdown = wrangling.synonym_breakdown(df, relations_df)

    angst = breakdown[breakdown["seed_tag"] == "Angst"]
    by_via = {row["matched_via"]: row for _, row in angst.iterrows()}
    check("Angst breakdown has literal / Heavy Angst / unidentified rows",
          set(by_via) == {"literal", "Heavy Angst", "unidentified"},
          f"got {sorted(by_via)}")
    check("Angst literal row counts 2 works at 50.0%",
          by_via["literal"]["n_works"] == 2 and by_via["literal"]["pct"] == 50.0,
          f"got {by_via['literal'].to_dict()}")
    check("Heavy Angst synonym row counts 1 work at 25.0%",
          by_via["Heavy Angst"]["n_works"] == 1 and by_via["Heavy Angst"]["pct"] == 25.0,
          f"got {by_via['Heavy Angst'].to_dict()}")
    check("unidentified row counts 1 work at 25.0%",
          by_via["unidentified"]["n_works"] == 1 and by_via["unidentified"]["pct"] == 25.0,
          f"got {by_via['unidentified'].to_dict()}")
    check("the metatag relation row is ignored",
          "Emotional Hurt" not in breakdown["matched_via"].values)

    bob = breakdown[breakdown["seed_tag"] == "Bob/Carol"]
    bob_by_via = {row["matched_via"]: row for _, row in bob.iterrows()}
    check("a seed tag with no relations still gets literal + unidentified rows",
          set(bob_by_via) == {"literal", "unidentified"}, f"got {sorted(bob_by_via)}")

    check("within a seed tag, the biggest slice comes first",
          angst.iloc[0]["matched_via"] == "literal", f"got {angst['matched_via'].tolist()}")


def run_cli_checks(tmpdir, script_path):
    csv_path = os.path.join(tmpdir, "cli_metadata.csv")
    write_fixture_csv(csv_path)
    relations_path = os.path.join(tmpdir, "cli_relations.csv")
    write_relations_csv(relations_path)

    parser = wrangling.build_arg_parser()
    default_args = parser.parse_args([])
    check("--input defaults to ao3_tag_metadata.csv",
          default_args.input == "ao3_tag_metadata.csv")
    check("--synonyms-csv defaults to ao3_tag_synonyms.csv",
          default_args.synonyms_csv == "ao3_tag_synonyms.csv")
    check("--literal-out defaults to ao3_seed_tag_literal_usage.csv",
          default_args.literal_out == "ao3_seed_tag_literal_usage.csv")
    check("--breakdown-out defaults to ao3_seed_tag_synonym_breakdown.csv",
          default_args.breakdown_out == "ao3_seed_tag_synonym_breakdown.csv")

    # With the relations CSV: both outputs written.
    literal_out = os.path.join(tmpdir, "literal.csv")
    breakdown_out = os.path.join(tmpdir, "breakdown.csv")
    result = subprocess.run(
        [sys.executable, script_path, "--input", csv_path,
         "--synonyms-csv", relations_path,
         "--literal-out", literal_out, "--breakdown-out", breakdown_out],
        capture_output=True, text=True,
    )
    check("main() with a relations CSV exits 0", result.returncode == 0,
          f"stderr: {result.stderr}")
    check("main() writes the literal-usage CSV", os.path.exists(literal_out))
    check("main() writes the breakdown CSV", os.path.exists(breakdown_out))
    with open(literal_out, newline="", encoding="utf-8") as f:
        literal_rows = {row["seed_tag"]: row for row in csv.DictReader(f)}
    check("CLI literal-usage CSV matches the direct function call",
          literal_rows["Angst"]["literal_pct"] == "50.0"
          and literal_rows["Angst"]["n_works"] == "4",
          f"got {literal_rows['Angst']}")
    with open(breakdown_out, newline="", encoding="utf-8") as f:
        breakdown_rows = list(csv.DictReader(f))
    check("CLI breakdown CSV includes the Heavy Angst synonym row",
          any(row["seed_tag"] == "Angst" and row["matched_via"] == "Heavy Angst"
              and row["n_works"] == "1" for row in breakdown_rows),
          f"got {breakdown_rows}")

    # Without the relations CSV: literal-usage still written, breakdown
    # skipped with a note, exit 0.
    no_rel_dir = os.path.join(tmpdir, "no_relations")
    os.makedirs(no_rel_dir, exist_ok=True)
    literal_out2 = os.path.join(no_rel_dir, "literal.csv")
    breakdown_out2 = os.path.join(no_rel_dir, "breakdown.csv")
    result2 = subprocess.run(
        [sys.executable, script_path, "--input", csv_path,
         "--synonyms-csv", os.path.join(no_rel_dir, "nonexistent.csv"),
         "--literal-out", literal_out2, "--breakdown-out", breakdown_out2],
        capture_output=True, text=True,
    )
    check("main() without a relations CSV exits 0", result2.returncode == 0,
          f"stderr: {result2.stderr}")
    check("literal-usage CSV is still written without a relations CSV",
          os.path.exists(literal_out2))
    check("breakdown is skipped with a note when the relations CSV is absent",
          not os.path.exists(breakdown_out2) and "skipping" in result2.stderr,
          f"stderr: {result2.stderr}")

    # A malformed relations CSV (missing columns) is a hard error.
    bad_relations = os.path.join(tmpdir, "bad_relations.csv")
    with open(bad_relations, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(["seed_tag", "wrong_column"])
    result3 = subprocess.run(
        [sys.executable, script_path, "--input", csv_path,
         "--synonyms-csv", bad_relations,
         "--literal-out", os.path.join(tmpdir, "l3.csv"),
         "--breakdown-out", os.path.join(tmpdir, "b3.csv")],
        capture_output=True, text=True,
    )
    check("a relations CSV missing required columns exits non-zero with an error",
          result3.returncode != 0 and "missing columns" in result3.stderr,
          f"rc={result3.returncode}, stderr: {result3.stderr}")


def main():
    tmpdir = tempfile.mkdtemp(prefix="ao3_wrangling_test_")
    script_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "ao3_tag_wrangling.py")

    run_literal_usage_checks(tmpdir)
    run_breakdown_checks(tmpdir)
    run_cli_checks(tmpdir, script_path)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
        sys.exit(1)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
