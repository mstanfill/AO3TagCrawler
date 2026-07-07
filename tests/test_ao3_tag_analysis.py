#!/usr/bin/env python3
"""Exercises ao3_tag_analysis.py end-to-end against synthetic metadata CSVs.

No network access needed -- this only reads/writes local files. Run with:
    python tests/test_ao3_tag_analysis.py
"""
import csv
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ao3_tag_visualizer as viz
import ao3_tag_analysis as analysis

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


def base_row(work_id, rating="Teen And Up Audiences", warnings="No Archive Warnings Apply",
             category="Gen", fandom="Fandom_A", relationship="", character="",
             additional_tags="", tag="Search_Tag"):
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
# Frequency-ranking fixture: additional_tags counts and their searched seed
# tag are, by design:
#   Common_Tag=10 (seed tag == "Common_Tag" -- also a seed tag itself)
#   Seed_Extra=4  (seed tag == "Seed_Extra" -- also a seed tag itself)
#   Medium_Tag=5, Tie_A=3, Tie_B=3, Rare_Tag=2, Singleton_A=1, Singleton_B=1
#     (all searched under the non-overlapping placeholder seed tag
#     "Search_Tag" -- never equal to their own additional_tags value)
#   (29 works total, work_ids 1-29)
#
# seed_tags = {"Common_Tag", "Seed_Extra", "Search_Tag"}, so Common_Tag and
# Seed_Extra are the only additional_tags values that also appear as a
# searched seed tag -- everything else is non-seed.
# ---------------------------------------------------------------------------

def build_frequency_fixture_rows():
    tag_counts = [
        ("Common_Tag", 10, "Common_Tag"),
        ("Seed_Extra", 4, "Seed_Extra"),
        ("Medium_Tag", 5, "Search_Tag"),
        ("Tie_A", 3, "Search_Tag"),
        ("Tie_B", 3, "Search_Tag"),
        ("Rare_Tag", 2, "Search_Tag"),
        ("Singleton_A", 1, "Search_Tag"),
        ("Singleton_B", 1, "Search_Tag"),
    ]
    rows = []
    work_id = 1
    for additional_tag, count, seed_tag in tag_counts:
        for _ in range(count):
            rows.append(base_row(work_id, tag=seed_tag, additional_tags=additional_tag))
            work_id += 1
    return rows


def write_frequency_fixture_csv(path):
    rows = build_frequency_fixture_rows()
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=METADATA_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return rows


# ---------------------------------------------------------------------------
# Clustering fixture: two mutually exclusive "blocks" spanning all 7 fields,
# plus one low-sample noise tag riding along with block A's other fields.
#
# Block A (work_ids 201-203): rating=Mature, warnings=No Archive Warnings
# Apply, category=F/M, fandom=Alpha, relationship=A/B, character=Bob,
# additional_tags=Whump.
# Block B (work_ids 204-206): rating=Teen And Up Audiences, warnings=Graphic
# Depictions Of Violence, category=Gen, fandom=Beta, relationship=<empty,
# exercises explode_field on a blank cell within the 7-field pool>,
# character=Carol, additional_tags=Angst.
# Noise (work_id 207): identical to block A except additional_tags=RareThing
# -- co-occurs with each of block A's other 6 tags exactly once.
#
# Hand-verified (see PASS/FAIL checks below): additional_tags::RareThing/
# character::Bob has joint_count=1, lift=1.75, pmi=0.8073... in raw stats,
# dropped by apply_min_pair_count(_, 2). At top_tags=13 (excludes RareThing,
# the lowest-count tag), cutting the dendrogram at n_clusters=2 recovers
# exactly the two blocks. At top_tags=8, keep_tags is the 6 always-4-count
# tags plus the two alphabetically-first count=3 tags (additional_tags::
# Angst, additional_tags::Whump).
# ---------------------------------------------------------------------------

def build_clustering_fixture_rows():
    rows = [
        base_row(201, "Mature", "No Archive Warnings Apply", "F/M", "Alpha", "A/B", "Bob", "Whump"),
        base_row(202, "Mature", "No Archive Warnings Apply", "F/M", "Alpha", "A/B", "Bob", "Whump"),
        base_row(203, "Mature", "No Archive Warnings Apply", "F/M", "Alpha", "A/B", "Bob", "Whump"),
        base_row(204, "Teen And Up Audiences", "Graphic Depictions Of Violence", "Gen", "Beta", "", "Carol", "Angst"),
        base_row(205, "Teen And Up Audiences", "Graphic Depictions Of Violence", "Gen", "Beta", "", "Carol", "Angst"),
        base_row(206, "Teen And Up Audiences", "Graphic Depictions Of Violence", "Gen", "Beta", "", "Carol", "Angst"),
        base_row(207, "Mature", "No Archive Warnings Apply", "F/M", "Alpha", "A/B", "Bob", "RareThing"),
    ]
    return rows


def write_clustering_fixture_csv(path):
    rows = build_clustering_fixture_rows()
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=METADATA_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return rows


def run_frequency_checks(tmpdir, script_path):
    csv_path = os.path.join(tmpdir, "frequency_metadata.csv")
    write_frequency_fixture_csv(csv_path)
    df = viz.load_metadata(csv_path)

    most_seed, most_non_seed, least = analysis.additional_tags_frequency(df, min_bottom_count=2)

    check("Common_Tag (also a seed tag) lands in most_frequent_seed",
          "Common_Tag" in most_seed["additional_tags"].values,
          f"got {most_seed['additional_tags'].tolist()}")
    check("Medium_Tag (never a seed tag) lands in most_frequent_non_seed",
          "Medium_Tag" in most_non_seed["additional_tags"].values,
          f"got {most_non_seed['additional_tags'].tolist()}")
    check("most_frequent_seed contains only Common_Tag and Seed_Extra",
          set(most_seed["additional_tags"]) == {"Common_Tag", "Seed_Extra"},
          f"got {most_seed['additional_tags'].tolist()}")
    check("most_frequent_seed is ordered by count descending (Common_Tag before Seed_Extra)",
          most_seed["additional_tags"].tolist() == ["Common_Tag", "Seed_Extra"],
          f"got {most_seed['additional_tags'].tolist()}")
    check("most-frequent non-seed additional_tags is Medium_Tag",
          most_non_seed.iloc[0]["additional_tags"] == "Medium_Tag",
          f"got {most_non_seed.iloc[0].to_dict()}")
    check("least-frequent (floor=2) additional_tags is Rare_Tag",
          least.iloc[0]["additional_tags"] == "Rare_Tag", f"got {least.iloc[0].to_dict()}")
    check("singletons excluded from least-frequent at default floor",
          "Singleton_A" not in least["additional_tags"].values
          and "Singleton_B" not in least["additional_tags"].values,
          f"got {least['additional_tags'].tolist()}")

    _, _, least_floor1 = analysis.additional_tags_frequency(df, min_bottom_count=1)
    check("singletons included when --frequency-min-count 1",
          "Singleton_A" in least_floor1["additional_tags"].values
          and "Singleton_B" in least_floor1["additional_tags"].values,
          f"got {least_floor1['additional_tags'].tolist()}")

    # Tie-break: Tie_A and Tie_B both count=3, alphabetically Tie_A first,
    # in both the descending (most_frequent_non_seed) and ascending (least)
    # sort orders.
    tied_most = most_non_seed[most_non_seed["additional_tags"].isin(["Tie_A", "Tie_B"])]
    check("most_frequent_non_seed tie-break is alphabetical (Tie_A before Tie_B)",
          tied_most["additional_tags"].tolist() == ["Tie_A", "Tie_B"],
          f"got {tied_most['additional_tags'].tolist()}")
    tied_least = least[least["additional_tags"].isin(["Tie_A", "Tie_B"])]
    check("least-frequent tie-break is alphabetical (Tie_A before Tie_B)",
          tied_least["additional_tags"].tolist() == ["Tie_A", "Tie_B"],
          f"got {tied_least['additional_tags'].tolist()}")

    # CLI: build_arg_parser defaults.
    parser = analysis.build_arg_parser()
    default_args = parser.parse_args(["--input", csv_path])
    check("--frequency-top-n defaults to 20", default_args.frequency_top_n == 20)
    check("--frequency-bottom-n defaults to 20", default_args.frequency_bottom_n == 20)
    check("--frequency-min-count defaults to 2", default_args.frequency_min_count == 2)
    check("--top-tags defaults to 60", default_args.top_tags == 60)
    check("--min-pair-count defaults to 2", default_args.min_pair_count == 2)
    check("--n-clusters defaults to 10", default_args.n_clusters == 10)
    check("--cluster-method defaults to average", default_args.cluster_method == "average")
    check("--cluster-heatmap-out defaults to None", default_args.cluster_heatmap_out is None)
    check("--frequency-only defaults to False", default_args.frequency_only is False)
    check("--clusters-only defaults to False", default_args.clusters_only is False)

    # CLI: --frequency-top-n/--frequency-bottom-n row counts and rank_type.
    cli_dir = os.path.join(tmpdir, "cli_run")
    os.makedirs(cli_dir, exist_ok=True)
    freq_out = os.path.join(cli_dir, "frequency.csv")
    result = subprocess.run(
        [sys.executable, script_path, "--input", csv_path, "--frequency-only",
         "--frequency-top-n", "3", "--frequency-bottom-n", "3", "--frequency-out", freq_out],
        capture_output=True, text=True,
    )
    check("main() --frequency-only exits 0", result.returncode == 0, f"stderr: {result.stderr}")
    with open(freq_out, newline="", encoding="utf-8") as f:
        cli_rows = list(csv.DictReader(f))
    # --frequency-top-n 3: most_frequent_seed only has 2 members (Common_Tag,
    # Seed_Extra) so head(3) returns both; most_frequent_non_seed returns 3;
    # --frequency-bottom-n 3: least_frequent returns 3. 2 + 3 + 3 = 8 rows.
    check("--frequency-top-n 3 --frequency-bottom-n 3 produces 8 rows",
          len(cli_rows) == 8, f"got {len(cli_rows)} rows: {cli_rows}")
    most_seed_tags = [r["additional_tags"] for r in cli_rows
                       if r["rank_type"] == "most_frequent_seed_tag"]
    most_non_seed_tags = [r["additional_tags"] for r in cli_rows
                           if r["rank_type"] == "most_frequent_non_seed_tag"]
    least_tags = [r["additional_tags"] for r in cli_rows if r["rank_type"] == "least_frequent"]
    check("CLI most_frequent_seed_tag is [Common_Tag, Seed_Extra]",
          most_seed_tags == ["Common_Tag", "Seed_Extra"], f"got {most_seed_tags}")
    check("CLI top-3 most_frequent_non_seed_tag is [Medium_Tag, Tie_A, Tie_B]",
          most_non_seed_tags == ["Medium_Tag", "Tie_A", "Tie_B"], f"got {most_non_seed_tags}")
    check("CLI bottom-3 least_frequent is [Rare_Tag, Tie_A, Tie_B]",
          least_tags == ["Rare_Tag", "Tie_A", "Tie_B"], f"got {least_tags}")

    # --frequency-only / --clusters-only narrowing.
    check("--frequency-only does NOT produce the clusters CSV",
          not os.path.exists(os.path.join(cli_dir, "ao3_tag_clusters.csv")))
    check("--frequency-only does NOT produce the cluster heatmap",
          not os.path.exists(os.path.join(cli_dir, "heatmaps", "heatmap_clusters.png")))

    clusters_only_dir = os.path.join(tmpdir, "clusters_only_run")
    os.makedirs(clusters_only_dir, exist_ok=True)
    clusters_only_freq_out = os.path.join(clusters_only_dir, "frequency.csv")
    result2 = subprocess.run(
        [sys.executable, script_path, "--input", csv_path, "--clusters-only",
         "--frequency-out", clusters_only_freq_out,
         "--heatmap-out-dir", os.path.join(clusters_only_dir, "heatmaps"),
         "--clusters-out", os.path.join(clusters_only_dir, "clusters.csv")],
        capture_output=True, text=True,
    )
    check("main() --clusters-only exits 0", result2.returncode == 0, f"stderr: {result2.stderr}")
    check("--clusters-only does NOT produce the frequency CSV",
          not os.path.exists(clusters_only_freq_out))


def run_clustering_checks(tmpdir, script_path):
    csv_path = os.path.join(tmpdir, "clustering_metadata.csv")
    write_clustering_fixture_csv(csv_path)
    df = viz.load_metadata(csv_path)

    tag_table = viz.build_document_tag_table(df, fields=analysis.ALL_METADATA_FIELDS)
    all_tags = set(tag_table["tag_id"].unique())
    check("clustering fixture has exactly 14 distinct tags", len(all_tags) == 14, f"got {all_tags}")
    for field in analysis.ALL_METADATA_FIELDS:
        check(f"document_tag_table includes at least one tag from {field} "
              "(regression check: 7-field pool, not the 4-field TAG_PAIR_FIELDS pool)",
              any(t.startswith(f"{field}::") for t in all_tags), f"got {all_tags}")

    incidence_all = viz.build_tag_incidence_matrix(tag_table, all_tags)
    n_docs = df["work_id"].nunique()
    check("clustering fixture has 7 distinct works", n_docs == 7)
    raw_stats = viz.tag_pair_statistics(incidence_all, n_docs)

    def pair_row(stats, a, b):
        return stats[((stats["tag_a"] == a) & (stats["tag_b"] == b)) |
                      ((stats["tag_a"] == b) & (stats["tag_b"] == a))]

    # Low-sample noise: RareThing co-occurs with each block-A tag exactly
    # once (joint=1, lift=1.75, pmi=0.807) -- present raw, dropped by
    # --min-pair-count 2.
    rare_bob = pair_row(raw_stats, "additional_tags::RareThing", "character::Bob")
    check("RareThing/Bob present in raw stats with joint_count=1",
          not rare_bob.empty and rare_bob["joint_count"].iloc[0] == 1, f"got {rare_bob}")
    filtered = viz.apply_min_pair_count(raw_stats, 2)
    rare_bob_filtered = pair_row(filtered, "additional_tags::RareThing", "character::Bob")
    check("RareThing/Bob dropped by --min-pair-count 2",
          rare_bob_filtered.empty, f"got {rare_bob_filtered}")

    # --top-tags truncation across all 7 fields: at k=8, the 6 always-4-count
    # tags plus the two alphabetically-first count=3 tags.
    pair_stats8, keep_tags8 = analysis.build_all_fields_pair_data(df, top_tags=8, min_pair_count=2)
    expected_top8 = {
        "category::F/M", "character::Bob", "fandom::Alpha", "rating::Mature",
        "relationship::A/B", "warnings::No Archive Warnings Apply",
        "additional_tags::Angst", "additional_tags::Whump",
    }
    check("--top-tags 8 keeps the expected 8 tags (alphabetical tie-break)",
          keep_tags8 == expected_top8, f"got {keep_tags8}")

    # NaN handling: display_matrix has NaN (block A / block B never
    # co-occur), fill_matrix does not (scipy linkage can't handle NaN).
    display_matrix, fill_matrix = analysis.build_cluster_matrix(pair_stats8, keep_tags8)
    check("display_matrix has NaN cells (cross-block pairs never observed)",
          display_matrix.isna().any().any())
    check("fill_matrix has no NaN cells (safe for scipy linkage)",
          not fill_matrix.isna().any().any())

    # Clustering recovers the two known blocks: exclude RareThing
    # (top_tags=13, the lowest-count tag) so its all-zero row can't muddy a
    # clean 2-cluster split, then cut at n_clusters=2.
    pair_stats13, keep_tags13 = analysis.build_all_fields_pair_data(df, top_tags=13, min_pair_count=2)
    check("--top-tags 13 excludes RareThing",
          "additional_tags::RareThing" not in keep_tags13, f"got {keep_tags13}")
    display13, fill13 = analysis.build_cluster_matrix(pair_stats13, keep_tags13)
    linkage_matrix = analysis.compute_linkage(fill13, method="average")
    check("compute_linkage returns a linkage matrix", linkage_matrix is not None)
    clusters_df = analysis.cut_clusters(linkage_matrix, list(fill13.index), n_clusters=2)
    block_a_tags = {"rating::Mature", "warnings::No Archive Warnings Apply", "category::F/M",
                     "fandom::Alpha", "relationship::A/B", "character::Bob", "additional_tags::Whump"}
    block_b_tags = {"rating::Teen And Up Audiences", "warnings::Graphic Depictions Of Violence",
                     "category::Gen", "fandom::Beta", "character::Carol", "additional_tags::Angst"}
    cluster_groups = clusters_df.groupby("cluster_id")["tag_id"].apply(set).tolist()
    check("n_clusters=2 recovers exactly the two known blocks",
          {frozenset(g) for g in cluster_groups} == {frozenset(block_a_tags), frozenset(block_b_tags)},
          f"got {cluster_groups}")

    # Full CLI run (defaults) produces all three outputs.
    full_dir = os.path.join(tmpdir, "full_run")
    os.makedirs(full_dir, exist_ok=True)
    freq_out = os.path.join(full_dir, "frequency.csv")
    heatmap_dir = os.path.join(full_dir, "heatmaps")
    clusters_out = os.path.join(full_dir, "clusters.csv")
    result = subprocess.run(
        [sys.executable, script_path, "--input", csv_path,
         "--frequency-out", freq_out, "--heatmap-out-dir", heatmap_dir,
         "--clusters-out", clusters_out, "--top-tags", "13", "--n-clusters", "2"],
        capture_output=True, text=True,
    )
    check("full default main() run exits 0", result.returncode == 0, f"stderr: {result.stderr}")
    heatmap_path = os.path.join(heatmap_dir, "heatmap_clusters.png")
    check("full run produces a non-empty cluster heatmap PNG",
          os.path.exists(heatmap_path) and os.path.getsize(heatmap_path) > 0)
    check("full run produces the clusters CSV", os.path.exists(clusters_out))
    check("full run produces the frequency CSV", os.path.exists(freq_out))

    with open(clusters_out, newline="", encoding="utf-8") as f:
        cli_clusters = list(csv.DictReader(f))
    cli_cluster_ids = {row["cluster_id"] for row in cli_clusters}
    check("--n-clusters 2 CLI run produces exactly 2 distinct cluster_id values",
          len(cli_cluster_ids) == 2, f"got {cli_cluster_ids}")
    cli_block_a = {row["tag_id"] for row in cli_clusters
                   if row["tag_id"] in block_a_tags}
    cli_block_b = {row["tag_id"] for row in cli_clusters
                   if row["tag_id"] in block_b_tags}
    cli_a_cluster_ids = {row["cluster_id"] for row in cli_clusters if row["tag_id"] in cli_block_a}
    cli_b_cluster_ids = {row["cluster_id"] for row in cli_clusters if row["tag_id"] in cli_block_b}
    check("CLI clusters CSV partitions block A and block B into different clusters",
          len(cli_a_cluster_ids) == 1 and len(cli_b_cluster_ids) == 1
          and cli_a_cluster_ids != cli_b_cluster_ids,
          f"block A cluster ids: {cli_a_cluster_ids}, block B cluster ids: {cli_b_cluster_ids}")


def main():
    tmpdir = tempfile.mkdtemp(prefix="ao3_analysis_test_")
    script_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "ao3_tag_analysis.py")

    run_frequency_checks(tmpdir, script_path)
    run_clustering_checks(tmpdir, script_path)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
        sys.exit(1)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
