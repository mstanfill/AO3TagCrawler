#!/usr/bin/env python3
"""Exercises ao3_tag_visualizer.py end-to-end against a synthetic metadata CSV.

No network access needed -- this only reads/writes local files. Run with:
    python tests/test_ao3_tag_visualizer.py
"""
import csv
import json
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ao3_tag_visualizer as viz

METADATA_FIELDS = [
    "tag", "work_id", "title", "author", "rating", "warnings", "category",
    "fandom", "relationship", "character", "additional_tags", "language",
    "series", "published", "status", "status_date", "words", "chapters",
    "comments", "kudos", "bookmarks", "hits", "summary",
]

RATINGS = ["General Audiences", "Teen And Up Audiences", "Mature", "Explicit", "Not Rated"]

FANDOMS = [f"Fandom_{i:02d}" for i in range(1, 26)]  # 25 fandoms
TAGS = [f"Tag_{i:02d}" for i in range(1, 46)]  # 45 additional tags

FAILURES = []


def check(name, condition, detail=""):
    if condition:
        print(f"PASS: {name}")
    else:
        print(f"FAIL: {name} {detail}")
        FAILURES.append(name)


def base_row(work_id, tag, rating, warnings, category, fandom, additional_tags):
    row = {field: "" for field in METADATA_FIELDS}
    row.update({
        "tag": tag,
        "work_id": work_id,
        "title": f"Work {work_id}",
        "author": "author",
        "rating": rating,
        "warnings": warnings,
        "category": category,
        "fandom": fandom,
        "relationship": "",
        "character": "",
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


def build_fixture_rows():
    rows = []
    work_id = 1

    # "Found Family" gets only 1 row -- should still appear as an all-zero
    # heatmap row / graph node after --min-count filtering.
    rows.append(base_row(work_id, "Found Family", "Mature", "No Archive Warnings Apply",
                          "Gen", FANDOMS[0], TAGS[0]))
    work_id += 1

    # Angst, Fluff, Time Travel, Canon Divergence: 6-8 rows each.
    tags_and_counts = [("Angst", 8), ("Fluff", 7), ("Time Travel", 6), ("Canon Divergence", 6)]
    for seed_tag, count in tags_and_counts:
        for i in range(count):
            rating = RATINGS[i % len(RATINGS)]
            warnings = ("Graphic Depictions Of Violence, Major Character Death"
                        if i == 0 else "No Archive Warnings Apply")
            category = "F/M, Gen" if i == 1 else "F/M"
            # Each seed tag cycles through the same first 2 fandoms, so
            # (seed_tag, fandom) pairs repeat enough to survive --min-count
            # filtering (a fandom appearing overall doesn't mean any single
            # seed tag pairs with it more than once, so this must be
            # deliberate rather than incidental).
            fandom = FANDOMS[i % 2]

            if i == 0:
                additional_tags = f"{TAGS[0]}, {TAGS[1]}, {TAGS[2]}"  # 3-value cell
            elif i == 1:
                additional_tags = ""  # empty cell edge case
            else:
                additional_tags = TAGS[i % 40]

            rows.append(base_row(work_id, seed_tag, rating, warnings, category,
                                  fandom, additional_tags))
            work_id += 1

    # Rows using the remaining fandoms exactly once each (23 of them), so
    # there are 25 distinct fandoms overall -- more than --top-fandoms'
    # default of 20, giving the top-N filter something to actually exclude.
    for i, fandom in enumerate(FANDOMS[2:], start=1):
        rows.append(base_row(work_id, "Angst", "Not Rated", "No Archive Warnings Apply",
                              "Gen", fandom, TAGS[40 + (i % 5)]))
        work_id += 1

    # Note: "Hurt/Comfort" is a plausible seed tag deliberately omitted
    # entirely, to model "a seed tag with zero scraped works".
    return rows


def write_fixture_csv(path):
    rows = build_fixture_rows()
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=METADATA_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return rows


def main():
    tmpdir = tempfile.mkdtemp(prefix="ao3_viz_test_")
    csv_path = os.path.join(tmpdir, "ao3_tag_metadata.csv")
    write_fixture_csv(csv_path)

    # 1. split_values
    check("split_values splits and strips",
          viz.split_values("Tag_01, Tag_02, Tag_03") == ["Tag_01", "Tag_02", "Tag_03"])
    check("split_values handles empty cell", viz.split_values("") == [])
    check("split_values dedupes", viz.split_values("Gen, Gen") == ["Gen"])

    df = viz.load_metadata(csv_path)
    check("load_metadata reads all rows", len(df) == len(build_fixture_rows()),
          f"got {len(df)}")

    # 2. explode_field: the 3-value additional_tags row must produce 3 rows
    exploded_tags = viz.explode_field(df, "additional_tags")
    three_value_work_ids = df[df["additional_tags"].str.count(",") == 2]["work_id"]
    check("explode_field has a 3-value row to test", len(three_value_work_ids) >= 1)
    for wid in three_value_work_ids:
        n = len(exploded_tags[exploded_tags["work_id"] == wid])
        check(f"explode_field: work {wid} with 3 tags -> 3 exploded rows", n == 3, f"got {n}")

    # empty additional_tags cell should contribute zero exploded rows
    empty_work_ids = df[df["additional_tags"] == ""]["work_id"]
    check("fixture has an empty additional_tags row", len(empty_work_ids) >= 1)
    for wid in empty_work_ids:
        n = len(exploded_tags[exploded_tags["work_id"] == wid])
        check(f"explode_field: work {wid} with empty tags -> 0 exploded rows", n == 0, f"got {n}")

    # 3. top-N filtering computed from the full dataset
    exploded_fandom = viz.explode_field(df, "fandom")
    full_counts = viz.total_value_counts(exploded_fandom, "fandom")
    keep = viz.top_n_values(exploded_fandom, "fandom", 20)
    check("top_n_values respects N", len(keep) <= 20, f"got {len(keep)}")
    check("top_n_values keeps a known-frequent fandom", FANDOMS[0] in keep)
    check("top_n_values excludes a known-rare fandom", FANDOMS[-1] not in keep)

    counts = viz.cooccurrence_counts(exploded_fandom, "fandom", keep)
    kept_fandom_row = counts[counts["fandom"] == FANDOMS[0]]
    expected_count = full_counts[FANDOMS[0]]
    check("cooccurrence count for a kept value matches full-dataset frequency",
          not kept_fandom_row.empty and kept_fandom_row["count"].sum() == expected_count,
          f"expected {expected_count}, got {kept_fandom_row}")

    # 4. min-count filtering + Found Family stays as a zero row
    warnings_exploded = viz.explode_field(df, "warnings")
    warnings_counts = viz.cooccurrence_counts(warnings_exploded, "warnings", None)
    filtered = viz.apply_min_count(warnings_counts, 2)
    found_family_edges = filtered[filtered["tag"] == "Found Family"]
    check("min-count drops Found Family's only-once edges", found_family_edges.empty)

    seed_tags = viz.rank_seed_tags(df, None)
    check("Found Family still ranked as a seed tag", "Found Family" in seed_tags)
    check("omitted seed tag never appears", "Hurt/Comfort" not in seed_tags)

    matrix = viz.cooccurrence_matrix(filtered, "warnings", seed_tags)
    check("heatmap matrix keeps Found Family as an all-zero row",
          "Found Family" in matrix.index and (matrix.loc["Found Family"] == 0).all())

    # 4b. --min-proportion: tells apart pairs that --min-count treats identically.
    # Found Family's only warnings pair is 1/1 of its works (100%); Angst's rare
    # "Graphic Depictions..." pair is 1 of Angst's many works (a few percent) --
    # both are just "count=1" to apply_min_count, but very different proportions.
    work_counts = df["tag"].value_counts()
    check("fixture: Found Family has exactly 1 work", work_counts["Found Family"] == 1)
    # The fixture's warnings cell "Graphic Depictions Of Violence, Major
    # Character Death" is itself a multi-value cell (delimiter ", "), so it
    # explodes into two separate single-value rows -- use one of those, not
    # the original joined string, which never appears in the exploded table.
    violence_warning = "Major Character Death"
    violence_row = warnings_counts[(warnings_counts["tag"] == "Angst") &
                                    (warnings_counts["warnings"] == violence_warning)]
    check("Angst's violence-warning pair has count 1",
          not violence_row.empty and violence_row["count"].iloc[0] == 1)
    angst_violence_proportion = violence_row["count"].iloc[0] / work_counts["Angst"]
    check("Angst's violence-warning pair is well under 50% of its works",
          angst_violence_proportion < 0.5, f"got {angst_violence_proportion}")

    by_count_1 = viz.apply_min_count(warnings_counts, 1)
    check("min-count=1 keeps Found Family's only warnings pair",
          not by_count_1[by_count_1["tag"] == "Found Family"].empty)
    check("min-count=1 also keeps Angst's rare violence-warning pair (can't tell them apart)",
          not by_count_1[(by_count_1["tag"] == "Angst") &
                          (by_count_1["warnings"] == violence_warning)].empty)

    by_prop_50 = viz.apply_min_proportion(warnings_counts, work_counts, 0.5)
    check("min-proportion=0.5 keeps Found Family's 100% pair",
          not by_prop_50[by_prop_50["tag"] == "Found Family"].empty)
    check("min-proportion=0.5 drops Angst's rare violence-warning pair "
          "(unlike min-count=1)",
          by_prop_50[(by_prop_50["tag"] == "Angst") &
                     (by_prop_50["warnings"] == violence_warning)].empty)

    field_tables_prop = viz.build_field_data(df, top_fandoms=20, top_additional_tags=40,
                                              work_counts=work_counts,
                                              min_count=None, min_proportion=0.5)
    prop_warnings = field_tables_prop["warnings"]
    check("build_field_data(proportion mode) keeps Found Family's warnings pair",
          not prop_warnings[prop_warnings["tag"] == "Found Family"].empty)
    check("build_field_data(proportion mode) drops Angst's rare violence-warning pair",
          prop_warnings[(prop_warnings["tag"] == "Angst") &
                         (prop_warnings["warnings"] == violence_warning)].empty)

    # 5. build_bipartite_graph
    field_tables = viz.build_field_data(df, top_fandoms=20, top_additional_tags=40, min_count=2)
    graph = viz.build_bipartite_graph(field_tables, seed_tags)
    check("graph has no node for the omitted seed tag", "tag::Hurt/Comfort" not in graph.nodes)
    check("graph has a node for Angst", "tag::Angst" in graph.nodes)
    check("graph has no empty-label nodes",
          all(graph.nodes[n].get("label", "") != "" for n in graph.nodes))

    if graph.has_edge("tag::Angst", "rating::Not Rated"):
        print("PASS: graph has an Angst<->rating edge with a weight")
    else:
        # Not fatal on its own -- rating isn't filtered, so this should exist
        # unless min_count excluded it; report for visibility.
        print("INFO: no tag::Angst <-> rating::Not Rated edge (may be below min_count)")

    # 6. render_network / render_heatmap produce real files
    network_path = os.path.join(tmpdir, "network.html")
    viz.render_network(graph, network_path)
    check("network HTML file created", os.path.exists(network_path) and os.path.getsize(network_path) > 0)
    with open(network_path, encoding="utf-8") as f:
        html = f.read()
    check("network HTML is self-contained (no external CDN reference)",
          "cdn.jsdelivr.net" not in html and "unpkg.com" not in html)

    # Filter-controls injection
    checkbox_class_count = html.count('class="ao3-cat-checkbox"')
    check("exactly one checkbox per FIELDS_TO_VISUALIZE entry",
          checkbox_class_count == len(viz.FIELDS_TO_VISUALIZE),
          f"got {checkbox_class_count}")
    for field in viz.FIELDS_TO_VISUALIZE:
        check(f"checkbox present for {field}", f'data-group="{field}"' in html)
    checkbox_tags = re.findall(r'<input[^>]*class="ao3-cat-checkbox"[^>]*>', html)
    check("all category checkboxes default-checked",
          len(checkbox_tags) == len(viz.FIELDS_TO_VISUALIZE)
          and all("checked" in tag for tag in checkbox_tags))

    check("ALL_SEED_TAGS declared", "const ALL_SEED_TAGS" in html)
    m = re.search(r"const ALL_SEED_TAGS = (\[.*?\]);", html, re.DOTALL)
    check("ALL_SEED_TAGS array is parseable", m is not None)
    if m:
        parsed = json.loads(m.group(1))
        check("ALL_SEED_TAGS matches fixture seed tags exactly",
              set(parsed) == set(seed_tags), f"got {parsed}")

    check("filter panel injected exactly once", html.count('id="ao3-filter-panel"') == 1)
    check("applyFilters defined exactly once", html.count("function applyFilters") == 1)
    check("exactly one <body>/</body> pair",
          html.count("<body>") == 1 and html.count("</body>") == 1)
    check("document still ends with </html>", html.rstrip().endswith("</html>"))
    check("tag-picker search input present", 'id="ao3-tag-search"' in html)
    check("tag-picker chips container present", 'id="ao3-tag-chips"' in html)
    check("tag-picker dropdown present", 'id="ao3-tag-dropdown"' in html)

    # Physics stabilize-then-stop. "stabilizationIterationsDone" and
    # "avoidOverlap" are also vis-network's own built-in identifiers, so they
    # legitimately appear inside the vendored library bundle too -- check our
    # own injected call signature (network.setOptions({ physics: false }))
    # instead, which vis-network's own minified code won't happen to match.
    check("stabilization listener present", "stabilizationIterationsDone" in html)
    check("avoidOverlap configured", '"avoidOverlap": 0.5' in html)
    check("improvedLayout disabled", '"improvedLayout": false' in html)
    check("stabilize-then-stop script injected exactly once",
          html.count("network.setOptions({ physics: false })") == 1)

    # HTML-special-character safety: a seed tag with &, <, " must round-trip
    # exactly through the JSON-encoded ALL_SEED_TAGS array (never through an
    # HTML string), since these characters are legal in real AO3 tag text.
    special_seed_tags = seed_tags + ['Tony & Steve <3 "Feels"']
    special_graph = viz.build_bipartite_graph(field_tables, special_seed_tags)
    special_network_path = os.path.join(tmpdir, "network_special_chars.html")
    viz.render_network(special_graph, special_network_path)
    with open(special_network_path, encoding="utf-8") as f:
        special_html = f.read()
    m2 = re.search(r"const ALL_SEED_TAGS = (\[.*?\]);", special_html, re.DOTALL)
    check("special-character tag round-trips through ALL_SEED_TAGS",
          m2 is not None and 'Tony & Steve <3 "Feels"' in json.loads(m2.group(1)))

    heatmap_dir = os.path.join(tmpdir, "heatmaps")
    os.makedirs(heatmap_dir, exist_ok=True)
    for field, counts in field_tables.items():
        matrix = viz.cooccurrence_matrix(counts, field, seed_tags)
        out_path = os.path.join(heatmap_dir, f"heatmap_{field}.png")
        viz.render_heatmap(matrix, field, out_path)
        check(f"heatmap PNG created for {field}",
              os.path.exists(out_path) and os.path.getsize(out_path) > 0)
        expected_cols = 20 if field == "fandom" else 40 if field == "additional_tags" else None
        if expected_cols is not None:
            check(f"{field} heatmap respects top-N column cap",
                  matrix.shape[1] <= expected_cols, f"got {matrix.shape[1]}")

    # 6b. Heatmaps color/label by percentage of each seed tag's works by
    # default now (no --normalize flag needed) -- confirm with a known cell:
    # Found Family has exactly 1 work, rated "Mature", so its normalized
    # rating matrix cell must be 100.0 (1/1 * 100), not the raw count 1.
    # Uses unfiltered counts (not field_tables["rating"], which is already
    # min_count=2-filtered and would drop Found Family's count=1 pair before
    # normalization ever ran -- the same reason the earlier min-count test
    # needed unfiltered warnings_counts too).
    rating_exploded = viz.explode_field(df, "rating")
    rating_counts_unfiltered = viz.cooccurrence_counts(rating_exploded, "rating", None)
    rating_matrix_normalized = viz.cooccurrence_matrix(
        rating_counts_unfiltered, "rating", seed_tags, normalize_by=work_counts)
    check("Found Family x Mature normalizes to 100.0%",
          rating_matrix_normalized.loc["Found Family", "Mature"] == 100.0,
          f"got {rating_matrix_normalized.loc['Found Family', 'Mature']}")
    rating_matrix_raw = viz.cooccurrence_matrix(rating_counts_unfiltered, "rating", seed_tags)
    check("...vs. raw count of 1 (sanity check the two modes actually differ)",
          rating_matrix_raw.loc["Found Family", "Mature"] == 1,
          f"got {rating_matrix_raw.loc['Found Family', 'Mature']}")

    # 7. Full main() invocation via subprocess
    out_dir = os.path.join(tmpdir, "full_run")
    os.makedirs(out_dir, exist_ok=True)
    network_out = os.path.join(out_dir, "ao3_tag_network.html")
    heatmap_out_dir = os.path.join(out_dir, "heatmaps")
    script_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "ao3_tag_visualizer.py")
    result = subprocess.run(
        [sys.executable, script_path, "--input", csv_path,
         "--network-out", network_out, "--heatmap-out-dir", heatmap_out_dir],
        capture_output=True, text=True,
    )
    check("main() subprocess exits 0", result.returncode == 0,
          f"stderr: {result.stderr}")
    check("main() produced the network HTML", os.path.exists(network_out))
    for field in viz.FIELDS_TO_VISUALIZE:
        p = os.path.join(heatmap_out_dir, f"heatmap_{field}.png")
        check(f"main() produced heatmap for {field}", os.path.exists(p))

    # 8. --min-proportion CLI: mutual exclusivity and range validation.
    # Deliberately uses "--min-count 2" -- the same value as its own default --
    # since argparse's mutually-exclusive-group conflict detection compares
    # the parsed value against each argument's *default*, not whether it was
    # actually typed on the command line. If --min-count's default were still
    # 2 (instead of None, applied later in main()), this exact case would
    # silently NOT raise a conflict even though both flags were explicitly
    # given -- this test would have caught that regression.
    both_flags_result = subprocess.run(
        [sys.executable, script_path, "--input", csv_path,
         "--min-count", "2", "--min-proportion", "0.1",
         "--network-out", os.path.join(tmpdir, "unused_network.html"),
         "--heatmap-out-dir", os.path.join(tmpdir, "unused_heatmaps")],
        capture_output=True, text=True,
    )
    check("passing both --min-count and --min-proportion exits nonzero",
          both_flags_result.returncode != 0, f"stderr: {both_flags_result.stderr}")
    check("error message names the conflicting flags",
          "--min-proportion" in both_flags_result.stderr
          and "--min-count" in both_flags_result.stderr,
          f"stderr: {both_flags_result.stderr}")

    bad_range_result = subprocess.run(
        [sys.executable, script_path, "--input", csv_path, "--min-proportion", "1.5",
         "--network-out", os.path.join(tmpdir, "unused_network2.html"),
         "--heatmap-out-dir", os.path.join(tmpdir, "unused_heatmaps2")],
        capture_output=True, text=True,
    )
    check("--min-proportion outside [0,1] exits nonzero", bad_range_result.returncode != 0)

    # 9. Regression safety: omitting both flags preserves today's default behavior.
    parser = viz.build_arg_parser()
    default_args = parser.parse_args(["--input", csv_path])
    check("neither flag parses to min_count=None, min_proportion=None (pre-effective-default)",
          default_args.min_count is None and default_args.min_proportion is None)
    if default_args.min_count is None and default_args.min_proportion is None:
        default_args.min_count = 2
    check("effective default resolves to min_count=2", default_args.min_count == 2)

    field_tables_default = viz.build_field_data(df, top_fandoms=20, top_additional_tags=40,
                                                 work_counts=None, min_count=2,
                                                 min_proportion=None)
    field_tables_old_style_call = viz.build_field_data(df, top_fandoms=20,
                                                        top_additional_tags=40, min_count=2)
    for field in viz.FIELDS_TO_VISUALIZE:
        check(f"default-mode field_tables[{field}] matches old min_count=2-only call",
              field_tables_default[field].equals(field_tables_old_style_call[field]))

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
        sys.exit(1)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
