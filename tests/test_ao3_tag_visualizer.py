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


def tag_pair_row(work_id, tag, fandom, character, relationship, additional_tags):
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


def build_tag_pair_fixture_rows():
    """A dedicated fixture (separate from build_fixture_rows(), never
    shared) for --tag-pairs: 10 distinct works (101-110), one (101)
    deliberately duplicated across two seed tags with identical other
    fields, to exercise work-level deduplication -- something the main
    fixture has no need for and deliberately doesn't have.

    Document frequencies (9 distinct tag_ids, n_docs=10):
      fandom::Alpha=5, fandom::Beta=5, additional_tags::Whump=5,
      character::Bob=5, additional_tags::Hurt=3, relationship::A/B=3,
      additional_tags::Fluffy=1, additional_tags::RareTag=1,
      character::Rarely=1.
    """
    rows = [
        tag_pair_row(101, "Angst", "Alpha", "", "", "Whump"),
        tag_pair_row(101, "Fluff", "Alpha", "", "", "Whump"),  # same work, 2nd seed tag
        tag_pair_row(102, "Angst", "Alpha", "", "", "Whump"),
        tag_pair_row(103, "Angst", "Alpha", "", "", "Whump"),
        tag_pair_row(104, "Angst", "Alpha", "", "", "Whump"),
        tag_pair_row(105, "Angst", "Alpha", "", "A/B", "Fluffy, Hurt"),
        tag_pair_row(106, "Angst", "Beta", "Bob", "", ""),
        tag_pair_row(107, "Angst", "Beta", "Bob", "", ""),
        tag_pair_row(108, "Angst", "Beta", "Bob", "", "Whump"),
        tag_pair_row(109, "Angst", "Beta", "Bob, Rarely", "A/B", "RareTag, Hurt"),
        tag_pair_row(110, "Angst", "Beta", "Bob", "A/B", "Hurt"),
    ]
    return rows


def write_tag_pair_fixture_csv(path):
    rows = build_tag_pair_fixture_rows()
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=METADATA_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return rows


def run_tag_pair_checks(tmpdir, script_path):
    csv_path = os.path.join(tmpdir, "tag_pair_metadata.csv")
    write_tag_pair_fixture_csv(csv_path)
    df = viz.load_metadata(csv_path)

    # 1. Work-level dedup: 11 CSV rows, but 10 distinct works.
    check("tag-pair fixture has 11 CSV rows", len(df) == 11)
    check("tag-pair fixture has 10 distinct work_ids", df["work_id"].nunique() == 10)

    tag_table = viz.build_document_tag_table(df)
    incidence_all = viz.build_tag_incidence_matrix(
        tag_table, set(tag_table["tag_id"].unique()))
    check("incidence matrix has 10 document rows (not 11)", incidence_all.shape[0] == 10)
    check("fandom::Alpha document count is 5 (not 6)",
          incidence_all["fandom::Alpha"].sum() == 5,
          f"got {incidence_all['fandom::Alpha'].sum()}")

    n_docs = df["work_id"].nunique()
    all_tags = set(tag_table["tag_id"].unique())
    check("tag-pair fixture has exactly 9 distinct tags", len(all_tags) == 9, f"got {all_tags}")

    raw_stats = viz.tag_pair_statistics(incidence_all, n_docs)

    def pair_row(stats, a, b):
        match = stats[((stats["tag_a"] == a) & (stats["tag_b"] == b)) |
                       ((stats["tag_a"] == b) & (stats["tag_b"] == a))]
        return match

    # 2. "Boring middle": lift=1.6, pmi=log2(1.6)=0.678 -- passes min_pair_count
    # (joint=4>=2) but sits inside the default (-1.0, 1.0) exclusion band.
    alpha_whump = pair_row(raw_stats, "fandom::Alpha", "additional_tags::Whump")
    check("Alpha/Whump joint count is 4", not alpha_whump.empty
          and alpha_whump["joint_count"].iloc[0] == 4,
          f"got {alpha_whump}")
    check("Alpha/Whump lift is 1.6",
          not alpha_whump.empty and abs(alpha_whump["lift"].iloc[0] - 1.6) < 1e-9,
          f"got {alpha_whump['lift'].iloc[0] if not alpha_whump.empty else None}")

    # 3. Zero-joint-count pair: Alpha's works (101-105) and Bob's works
    # (106-110) are disjoint -- must be entirely absent, never -inf.
    alpha_bob = pair_row(raw_stats, "fandom::Alpha", "character::Bob")
    check("fandom::Alpha / character::Bob (joint=0) is absent from raw stats",
          alpha_bob.empty, f"got {alpha_bob}")

    # 4. Low-sample noise: RareTag/Rarely co-occur in only work 109 (joint=1),
    # giving an extreme but meaningless lift=10.0 (pmi=log2(10)=3.32) -- present
    # in raw stats, but must be dropped once --min-pair-count is applied.
    rare_pair = pair_row(raw_stats, "additional_tags::RareTag", "character::Rarely")
    check("RareTag/Rarely present in raw stats with lift=10.0",
          not rare_pair.empty and abs(rare_pair["lift"].iloc[0] - 10.0) < 1e-9,
          f"got {rare_pair}")
    filtered_min_count = viz.apply_min_pair_count(raw_stats, 2)
    rare_pair_filtered = pair_row(filtered_min_count, "additional_tags::RareTag", "character::Rarely")
    check("RareTag/Rarely dropped by --min-pair-count 2 despite high lift",
          rare_pair_filtered.empty, f"got {rare_pair_filtered}")

    # 5. A pair that survives everything: Hurt/A-B, joint=3, lift=10/3=3.333,
    # pmi=log2(3.333)=1.737 -- passes min_pair_count and the default min_pmi=1.0.
    hurt_ab = pair_row(raw_stats, "additional_tags::Hurt", "relationship::A/B")
    check("Hurt/A-B joint count is 3 and lift is 10/3",
          not hurt_ab.empty and hurt_ab["joint_count"].iloc[0] == 3
          and abs(hurt_ab["lift"].iloc[0] - 10 / 3) < 1e-9,
          f"got {hurt_ab}")
    pair_stats_default, keep_tags_default = viz.build_tag_pair_data(
        df, top_tags=40, min_pair_count=2, min_pmi=1.0, max_pmi=-1.0)
    check("Hurt/A-B survives default min_pair_count/min_pmi filtering",
          not pair_row(pair_stats_default, "additional_tags::Hurt", "relationship::A/B").empty)
    check("Alpha/Whump (boring middle) is excluded by default thresholds",
          pair_row(pair_stats_default, "fandom::Alpha", "additional_tags::Whump").empty)

    # 6. Top-K tie-break: at k=5, the four count=5 tags plus, of the two
    # count=3 tags tied for the 5th slot, the alphabetically-first one
    # ("additional_tags::Hurt" < "relationship::A/B").
    top5 = viz.top_k_tags_by_document_frequency(tag_table, 5)
    expected_top5 = {"fandom::Alpha", "fandom::Beta", "additional_tags::Whump",
                      "character::Bob", "additional_tags::Hurt"}
    check("top_k_tags_by_document_frequency(k=5) picks the alphabetical tie-winner",
          top5 == expected_top5, f"got {top5}")
    check("relationship::A/B excluded at k=5 despite tying on count",
          "relationship::A/B" not in top5)

    # 7. tag_pair_matrix all-NaN row: RareTag's only pairs all have joint_count=1
    # (it appears in exactly 1 document), so every one is dropped by
    # --min-pair-count 2, leaving it with zero surviving pairs.
    matrix = viz.tag_pair_matrix(pair_stats_default, keep_tags_default)
    if "additional_tags::RareTag" in matrix.index:
        check("RareTag is an all-NaN row after its only pairs are filtered out",
              matrix.loc["additional_tags::RareTag"].isna().all())
    else:
        check("RareTag excluded from top-40 keep_tags (fixture too small to hit this "
              "case) -- adjust top_tags if this ever fires", False,
              "RareTag should be in keep_tags at top_tags=40 for a 9-tag universe")

    # 8. CLI wiring / opt-in gating.
    parser = viz.build_arg_parser()
    default_cli_args = parser.parse_args(["--input", csv_path])
    check("--tag-pairs defaults to False", default_cli_args.tag_pairs is False)
    check("--top-tags defaults to 40", default_cli_args.top_tags == 40)
    check("--min-pair-count defaults to 2", default_cli_args.min_pair_count == 2)
    check("--min-pmi defaults to 1.0", default_cli_args.min_pmi == 1.0)
    check("--max-pmi defaults to -1.0", default_cli_args.max_pmi == -1.0)
    check("--tag-pair-heatmap-out defaults to None", default_cli_args.tag_pair_heatmap_out is None)

    no_flag_dir = os.path.join(tmpdir, "no_tag_pairs_run")
    os.makedirs(no_flag_dir, exist_ok=True)
    no_flag_network = os.path.join(no_flag_dir, "network.html")
    no_flag_heatmap_dir = os.path.join(no_flag_dir, "heatmaps")
    no_flag_tag_pair_network = os.path.join(no_flag_dir, "ao3_tag_pair_network.html")
    result_no_flag = subprocess.run(
        [sys.executable, script_path, "--input", csv_path,
         "--network-out", no_flag_network, "--heatmap-out-dir", no_flag_heatmap_dir,
         "--tag-pair-network-out", no_flag_tag_pair_network],
        capture_output=True, text=True,
    )
    check("main() without --tag-pairs exits 0", result_no_flag.returncode == 0,
          f"stderr: {result_no_flag.stderr}")
    check("main() without --tag-pairs does NOT produce the tag-pair network file",
          not os.path.exists(no_flag_tag_pair_network))
    check("main() without --tag-pairs does NOT produce the tag-pair heatmap",
          not os.path.exists(os.path.join(no_flag_heatmap_dir, "heatmap_tag_pairs.png")))

    with_flag_dir = os.path.join(tmpdir, "with_tag_pairs_run")
    os.makedirs(with_flag_dir, exist_ok=True)
    with_flag_network = os.path.join(with_flag_dir, "network.html")
    with_flag_heatmap_dir = os.path.join(with_flag_dir, "heatmaps")
    with_flag_tag_pair_network = os.path.join(with_flag_dir, "ao3_tag_pair_network.html")
    result_with_flag = subprocess.run(
        [sys.executable, script_path, "--input", csv_path, "--tag-pairs",
         "--network-out", with_flag_network, "--heatmap-out-dir", with_flag_heatmap_dir,
         "--tag-pair-network-out", with_flag_tag_pair_network],
        capture_output=True, text=True,
    )
    check("main() with --tag-pairs exits 0", result_with_flag.returncode == 0,
          f"stderr: {result_with_flag.stderr}")
    check("main() with --tag-pairs produces the tag-pair network file",
          os.path.exists(with_flag_tag_pair_network))
    tag_pair_heatmap_path = os.path.join(with_flag_heatmap_dir, "heatmap_tag_pairs.png")
    check("main() with --tag-pairs produces the tag-pair heatmap",
          os.path.exists(tag_pair_heatmap_path))
    with open(with_flag_tag_pair_network, encoding="utf-8") as f:
        tag_pair_html = f.read()
    # fandom::Beta survives default filtering (it pairs with character::Bob:
    # joint=5, lift=2.0, pmi=1.0, clearing the default min_pmi=1.0 bar) --
    # unlike fandom::Alpha, whose only pairs are either below --min-pair-count
    # or inside the default "boring middle" exclusion band.
    check("tag-pair network HTML contains a namespaced node id",
          '"id": "fandom::Beta"' in tag_pair_html)
    check("tag-pair network HTML contains lift/pmi in a hover title",
          "lift=" in tag_pair_html and "pmi=" in tag_pair_html)
    for field in viz.TAG_PAIR_FIELDS:
        check(f"tag-pair network HTML has a checkbox for {field}",
              f'data-group="{field}"' in tag_pair_html)

    # 9. Soft-warning: inverted/overlapping thresholds shouldn't be fatal.
    warn_result = subprocess.run(
        [sys.executable, script_path, "--input", csv_path, "--tag-pairs",
         "--min-pmi", "0", "--max-pmi", "0",
         "--network-out", os.path.join(tmpdir, "warn_network.html"),
         "--heatmap-out-dir", os.path.join(tmpdir, "warn_heatmaps"),
         "--tag-pair-network-out", os.path.join(tmpdir, "warn_tag_pair_network.html")],
        capture_output=True, text=True,
    )
    check("--min-pmi <= --max-pmi is a non-fatal warning, not an error",
          warn_result.returncode == 0, f"stderr: {warn_result.stderr}")
    check("warning text mentions --min-pmi and --max-pmi",
          "--min-pmi" in warn_result.stderr and "--max-pmi" in warn_result.stderr,
          f"stderr: {warn_result.stderr}")

    # 10. Order-independence: tag_pair_statistics must canonicalize
    # tag_a/tag_b itself, not merely inherit alphabetical order from a
    # caller that happens to pre-sort its incidence matrix's columns.
    # build_tag_incidence_matrix always sorts, so this is the only way to
    # actually exercise that guarantee -- feed a hand-built incidence
    # matrix with columns in reverse alphabetical order directly.
    unsorted_incidence = viz.pd.DataFrame(
        {"warnings::Z": [1, 1, 1, 0], "additional_tags::A": [1, 0, 0, 1]},
        index=["work1", "work2", "work3", "work4"],
    ).astype("int8")
    order_stats = viz.tag_pair_statistics(unsorted_incidence, n_docs=4)
    check("tag_pair_statistics canonicalizes tag_a/tag_b even with unsorted incidence columns",
          not order_stats.empty
          and order_stats.iloc[0]["tag_a"] == "additional_tags::A"
          and order_stats.iloc[0]["tag_b"] == "warnings::Z",
          f"got {order_stats.to_dict('records')}")
    check("count_a/count_b stay correctly paired with the canonicalized tag_a/tag_b",
          order_stats.iloc[0]["count_a"] == 2 and order_stats.iloc[0]["count_b"] == 3,
          f"got {order_stats.to_dict('records')}")


def run_large_scale_sparsity_checks():
    """Regression guard for the MemoryError a real --all-tags run hit: with
    81,037 tags, the old dense tags x tags joint matrix needed 48.9 GiB
    (81,037**2 * 8 bytes). tag_pair_statistics now computes joint via
    scipy.sparse instead of a dense np.triu_indices sweep. This builds a
    15,000-tag, 1,000-document incidence matrix (each document holding only
    a handful of tags, matching realistic freeform-tag sparsity) -- a scale
    where the old dense approach would need ~1.8 GiB just for the joint
    matrix (15,000**2 * 8 bytes) despite the real data being tiny -- and
    confirms both correctness (via two deliberately planted, hand-countable
    tags) and that it completes quickly rather than attempting a huge
    allocation."""
    import time

    import numpy as np

    rng = np.random.default_rng(1234)
    n_tags, n_docs, tags_per_doc = 15000, 1000, 8
    tag_names = [f"additional_tags::Tag_{i}" for i in range(n_tags)]

    rows = []
    for work_id in range(n_docs):
        chosen = rng.choice(n_tags, size=tags_per_doc, replace=False)
        for c in chosen:
            rows.append((f"work{work_id}", tag_names[c]))
    # Plant two known tags co-occurring in exactly 50 documents, for a
    # hand-verifiable correctness check at this scale.
    for work_id in range(50):
        rows.append((f"planted{work_id}", "additional_tags::KnownA"))
        rows.append((f"planted{work_id}", "additional_tags::KnownB"))

    tag_table = viz.pd.DataFrame(rows, columns=["work_id", "tag_id"])
    keep_tags = set(tag_names) | {"additional_tags::KnownA", "additional_tags::KnownB"}
    total_docs = n_docs + 50

    incidence = viz.build_tag_incidence_matrix(tag_table, keep_tags)
    start = time.time()
    stats = viz.tag_pair_statistics(incidence, n_docs=total_docs)
    elapsed = time.time() - start

    check("large-scale (15,000-tag) tag_pair_statistics completes quickly "
          "(would be a huge/slow dense allocation under the old approach)",
          elapsed < 30, f"took {elapsed:.1f}s")

    known_pair = stats[((stats["tag_a"] == "additional_tags::KnownA")
                         & (stats["tag_b"] == "additional_tags::KnownB"))
                        | ((stats["tag_a"] == "additional_tags::KnownB")
                           & (stats["tag_b"] == "additional_tags::KnownA"))]
    check("planted KnownA/KnownB pair is present with the correct joint_count at scale",
          not known_pair.empty and known_pair.iloc[0]["joint_count"] == 50,
          f"got {known_pair.to_dict('records')}")
    check("planted pair is canonicalized alphabetically (KnownA < KnownB)",
          not known_pair.empty and known_pair.iloc[0]["tag_a"] == "additional_tags::KnownA",
          f"got {known_pair.to_dict('records')}")


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

    # 10. --tag-pairs: dedicated fixture, own section (see run_tag_pair_checks).
    run_tag_pair_checks(tmpdir, script_path)

    # 11. Large-scale sparsity regression (see run_large_scale_sparsity_checks).
    run_large_scale_sparsity_checks()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
        sys.exit(1)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
