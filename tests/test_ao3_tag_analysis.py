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
    check("--cluster-resolution defaults to 1.0", default_args.cluster_resolution == 1.0)
    check("--cluster-network-out defaults to ao3_tag_cluster_network.html",
          default_args.cluster_network_out == "ao3_tag_cluster_network.html")
    check("--cluster-meta-network-out defaults to ao3_tag_cluster_meta_network.html",
          default_args.cluster_meta_network_out == "ao3_tag_cluster_meta_network.html")
    check("--gexf-out defaults to None (opt-in)", default_args.gexf_out is None)
    check("--all-tags defaults to False", default_args.all_tags is False)
    check("--min-cluster-size defaults to 1", default_args.min_cluster_size == 1)
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
    check("--frequency-only does NOT produce the cluster network HTML",
          not os.path.exists(os.path.join(cli_dir, "ao3_tag_cluster_network.html")))
    check("--frequency-only does NOT produce the cluster meta-network HTML",
          not os.path.exists(os.path.join(cli_dir, "ao3_tag_cluster_meta_network.html")))

    clusters_only_dir = os.path.join(tmpdir, "clusters_only_run")
    os.makedirs(clusters_only_dir, exist_ok=True)
    clusters_only_freq_out = os.path.join(clusters_only_dir, "frequency.csv")
    result2 = subprocess.run(
        [sys.executable, script_path, "--input", csv_path, "--clusters-only",
         "--frequency-out", clusters_only_freq_out,
         "--cluster-network-out", os.path.join(clusters_only_dir, "cluster_network.html"),
         "--cluster-meta-network-out", os.path.join(clusters_only_dir, "meta_network.html"),
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

    # --all-tags: top_tags=None keeps every tag in the pool, not just the
    # top-K by document frequency (this Python-level path was previously
    # unreachable from the CLI, since --top-tags was numeric-only).
    pair_stats_all, keep_tags_all = analysis.build_all_fields_pair_data(
        df, top_tags=None, min_pair_count=2)
    check("top_tags=None (--all-tags) keeps all 14 tags",
          keep_tags_all == all_tags, f"got {keep_tags_all}")

    # Graph construction: every keep_tags8 tag is a node upfront, edges only
    # for pmi > 0 pairs.
    graph8 = analysis.build_cluster_graph(pair_stats8, keep_tags8)
    check("build_cluster_graph has a node for every kept tag",
          set(graph8.nodes) == keep_tags8, f"got {set(graph8.nodes)}")
    check("build_cluster_graph only adds edges for pmi > 0 pairs",
          all(data["pmi"] > 0 for _, _, data in graph8.edges(data=True)))

    # Community detection recovers the two known blocks: exclude RareThing
    # (top_tags=13, the lowest-count tag) so its isolated node can't muddy a
    # clean 2-community split.
    pair_stats13, keep_tags13 = analysis.build_all_fields_pair_data(df, top_tags=13, min_pair_count=2)
    check("--top-tags 13 excludes RareThing",
          "additional_tags::RareThing" not in keep_tags13, f"got {keep_tags13}")
    graph13 = analysis.build_cluster_graph(pair_stats13, keep_tags13)
    communities13 = analysis.detect_communities(graph13)
    block_a_tags = {"rating::Mature", "warnings::No Archive Warnings Apply", "category::F/M",
                     "fandom::Alpha", "relationship::A/B", "character::Bob", "additional_tags::Whump"}
    block_b_tags = {"rating::Teen And Up Audiences", "warnings::Graphic Depictions Of Violence",
                     "category::Gen", "fandom::Beta", "character::Carol", "additional_tags::Angst"}
    check("Louvain community detection recovers exactly the two known blocks",
          {frozenset(c) for c in communities13} == {frozenset(block_a_tags), frozenset(block_b_tags)},
          f"got {[set(c) for c in communities13]}")
    clusters_df = analysis.assign_cluster_ids(communities13)
    cluster_groups = clusters_df.groupby("cluster_id")["tag_id"].apply(set).tolist()
    check("assign_cluster_ids preserves the two known blocks",
          {frozenset(g) for g in cluster_groups} == {frozenset(block_a_tags), frozenset(block_b_tags)},
          f"got {cluster_groups}")

    # Full CLI run (defaults + --gexf-out) produces every output.
    full_dir = os.path.join(tmpdir, "full_run")
    os.makedirs(full_dir, exist_ok=True)
    freq_out = os.path.join(full_dir, "frequency.csv")
    network_out = os.path.join(full_dir, "cluster_network.html")
    meta_network_out = os.path.join(full_dir, "meta_network.html")
    gexf_out = os.path.join(full_dir, "network.gexf")
    clusters_out = os.path.join(full_dir, "clusters.csv")
    result = subprocess.run(
        [sys.executable, script_path, "--input", csv_path,
         "--frequency-out", freq_out, "--cluster-network-out", network_out,
         "--cluster-meta-network-out", meta_network_out,
         "--gexf-out", gexf_out,
         "--clusters-out", clusters_out, "--top-tags", "13"],
        capture_output=True, text=True,
    )
    check("full default main() run exits 0", result.returncode == 0, f"stderr: {result.stderr}")
    check("full run produces a non-empty cluster network HTML",
          os.path.exists(network_out) and os.path.getsize(network_out) > 0)
    check("full run produces a non-empty cluster meta-network HTML",
          os.path.exists(meta_network_out) and os.path.getsize(meta_network_out) > 0)
    check("--gexf-out produces a GEXF file", os.path.exists(gexf_out))
    check("full run produces the clusters CSV", os.path.exists(clusters_out))

    # The GEXF round-trips through networkx with the analysis attributes
    # intact -- this is what Gephi will read.
    gexf_graph = viz.nx.read_gexf(gexf_out)
    check("GEXF round-trips with all 13 tag nodes",
          gexf_graph.number_of_nodes() == 13, f"got {gexf_graph.number_of_nodes()}")
    gexf_bob = gexf_graph.nodes["character::Bob"]
    check("GEXF nodes carry label/field/cluster_id for Gephi partitioning",
          gexf_bob.get("field") == "character" and isinstance(gexf_bob.get("cluster_id"), int),
          f"got {gexf_bob}")
    gexf_edge = list(gexf_graph.edges(data=True))[0]
    check("GEXF edges carry weight/lift/joint_count",
          all(key in gexf_edge[2] for key in ("weight", "lift", "joint_count")),
          f"got {gexf_edge}")

    # Meta network on the two-block fixture: one meta node per cluster,
    # labeled with the block's fandom (Alpha/Beta at 100% co-occurrence),
    # and no edges (the blocks never co-occur).
    with open(meta_network_out, encoding="utf-8") as f:
        meta_html = f.read()
    check("meta network HTML has a node labeled with cluster 1's top fandom",
          "1: Alpha" in meta_html, "expected label '1: Alpha'")
    check("meta network HTML has a node labeled with cluster 2's top fandom",
          "2: Beta" in meta_html, "expected label '2: Beta'")
    check("meta network HTML keeps physics enabled (small graph, organic layout)",
          '"enabled": true' in meta_html or '"enabled":true' in meta_html)

    with open(network_out, encoding="utf-8") as f:
        network_html = f.read()
    check("cluster network HTML disables physics (static layout, not client-side simulation)",
          '"enabled": false' in network_html or '"enabled":false' in network_html)
    check("cluster network HTML does not inject the (now-meaningless) stabilize-then-stop script",
          'network.once("stabilizationIterationsDone"' not in network_html)
    check("cluster network HTML nodes carry fixed x/y positions",
          '"x":' in network_html.replace(" ", "") and '"y":' in network_html.replace(" ", ""))
    check("cluster network HTML bounds the checkbox panel's height (thousands of "
          "clusters would otherwise push the graph canvas below the fold)",
          "#ao3-cat-checkboxes { max-height:" in network_html)
    check("full run produces the frequency CSV", os.path.exists(freq_out))

    with open(clusters_out, newline="", encoding="utf-8") as f:
        cli_clusters = list(csv.DictReader(f))
    cli_cluster_ids = {row["cluster_id"] for row in cli_clusters}
    check("CLI run on the block fixture produces exactly 2 distinct cluster_id values",
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

    # --all-tags CLI run: every kept tag gets a row, including RareThing
    # (isolated node with zero surviving edges, per the existing "Found
    # Family stays as an all-zero row" precedent from tag_pair_matrix).
    all_tags_dir = os.path.join(tmpdir, "all_tags_run")
    os.makedirs(all_tags_dir, exist_ok=True)
    all_tags_clusters_out = os.path.join(all_tags_dir, "clusters.csv")
    result_all_tags = subprocess.run(
        [sys.executable, script_path, "--input", csv_path, "--clusters-only", "--all-tags",
         "--cluster-network-out", os.path.join(all_tags_dir, "cluster_network.html"),
         "--cluster-meta-network-out", os.path.join(all_tags_dir, "meta_network.html"),
         "--clusters-out", all_tags_clusters_out],
        capture_output=True, text=True,
    )
    check("main() --all-tags exits 0", result_all_tags.returncode == 0,
          f"stderr: {result_all_tags.stderr}")
    with open(all_tags_clusters_out, newline="", encoding="utf-8") as f:
        all_tags_rows = list(csv.DictReader(f))
    check("--all-tags CLI run includes all 14 tags in the clusters CSV",
          {row["tag_id"] for row in all_tags_rows} == all_tags,
          f"got {len(all_tags_rows)} rows: {[row['tag_id'] for row in all_tags_rows]}")


def _fully_connected_block(graph, tags, weight):
    for a in tags:
        for b in tags:
            if a < b:
                graph.add_edge(a, b, weight=weight)


def run_min_cluster_size_checks():
    # Direct unit-level test of merge_small_communities against a hand-built
    # nx.Graph, bypassing Louvain entirely -- same strategy as the
    # order-independence test in tests/test_ao3_tag_visualizer.py. Ten
    # synthetic tags in three designed blocks: X (4 tags), Y (4 tags), Z (2
    # tags), each block a fully-connected clique, no cross-block edges.
    import networkx as nx

    x_tags, y_tags, z_tags = ["X1", "X2", "X3", "X4"], ["Y1", "Y2", "Y3", "Y4"], ["Z1", "Z2"]
    graph = nx.Graph()
    graph.add_nodes_from(x_tags + y_tags + z_tags)
    _fully_connected_block(graph, x_tags, 3.0)
    _fully_connected_block(graph, y_tags, 3.0)
    _fully_connected_block(graph, z_tags, 3.0)
    communities = [set(x_tags), set(y_tags), set(z_tags)]

    # min_cluster_size=1: nothing is undersized, communities pass through
    # unchanged.
    merged1 = analysis.merge_small_communities(communities, graph, min_cluster_size=1)
    check("min_cluster_size=1 leaves the 3 designed blocks unchanged",
          {frozenset(c) for c in merged1} == {frozenset(x_tags), frozenset(y_tags), frozenset(z_tags)},
          f"got {[set(c) for c in merged1]}")

    # min_cluster_size=3: the 2-tag Z block is undersized and fully isolated
    # (zero cross-block edges), so it merges into the largest remaining
    # community -- X and Y tie at size 4, tie-broken alphabetically (X1 < Y1).
    merged3 = analysis.merge_small_communities(communities, graph, min_cluster_size=3)
    check("min_cluster_size=3 merges isolated Z into the largest remaining community",
          {frozenset(c) for c in merged3} == {frozenset(x_tags) | frozenset(z_tags), frozenset(y_tags)},
          f"got {[set(c) for c in merged3]}")
    check("every resulting community meets the minimum size",
          min(len(c) for c in merged3) >= 3, f"got sizes {[len(c) for c in merged3]}")

    # An impossible constraint (min_cluster_size larger than the total tag
    # count) collapses everything into a single community without erroring.
    merged_impossible = analysis.merge_small_communities(communities, graph, min_cluster_size=100)
    check("min_cluster_size larger than total tags collapses to a single community",
          len(merged_impossible) == 1, f"got {[set(c) for c in merged_impossible]}")
    check("no tag is lost when collapsing to a single community",
          merged_impossible[0] == set(x_tags) | set(y_tags) | set(z_tags),
          f"got {merged_impossible[0]}")

    # Weight-aware merge: a small undersized community W is connected to
    # both X and Y (not isolated), but more strongly to Y -- it should merge
    # into Y, proving edge weight (not just size) drives the merge target.
    weighted_graph = nx.Graph()
    w_tags = ["W1", "W2"]
    weighted_graph.add_nodes_from(x_tags + y_tags + w_tags)
    _fully_connected_block(weighted_graph, x_tags, 3.0)
    _fully_connected_block(weighted_graph, y_tags, 3.0)
    weighted_graph.add_edge("W1", "W2", weight=3.0)
    # W's total connection to X: 1.0. W's total connection to Y: 5.0.
    weighted_graph.add_edge("W1", "X1", weight=1.0)
    weighted_graph.add_edge("W2", "Y1", weight=2.0)
    weighted_graph.add_edge("W2", "Y2", weight=3.0)
    weighted_communities = [set(x_tags), set(y_tags), set(w_tags)]
    merged_weighted = analysis.merge_small_communities(
        weighted_communities, weighted_graph, min_cluster_size=3)
    check("undersized community connected to two others merges into the "
          "higher total-edge-weight one, not just the larger one",
          {frozenset(c) for c in merged_weighted} == {frozenset(x_tags), frozenset(y_tags) | frozenset(w_tags)},
          f"got {[set(c) for c in merged_weighted]}")


def run_large_scale_community_checks():
    # Regression test for the MemoryError this rewrite exists to fix:
    # ~40,000 tags with two planted, fully-connected 30-tag cliques embedded
    # in sparse random background edges. Builds pair_stats directly (no need
    # to re-drive the CSV/incidence pipeline, which the two prior sparse-
    # matrix fixes' regression tests already cover) and time-bounds
    # build_cluster_graph + detect_communities, matching the convention of
    # test_ao3_tag_visualizer.py's run_large_scale_sparsity_checks.
    import random
    import time

    rng = random.Random(0)
    n_tags = 40_000
    tags = [f"additional_tags::Tag{i}" for i in range(n_tags)]

    clique_a = tags[:30]
    clique_b = tags[30:60]

    rows = []
    for clique in (clique_a, clique_b):
        for i, a in enumerate(clique):
            for b in clique[i + 1:]:
                rows.append({"tag_a": a, "tag_b": b, "joint_count": 10,
                             "count_a": 10, "count_b": 10, "lift": 5.0, "pmi": 2.32})

    # Sparse random background noise: ~30,000 edges among all 40,000 tags,
    # deliberately excluding the planted cliques' own pairs so the cliques
    # stay the strongest signal in the graph. Kept well below AO3-scale edge
    # counts -- Louvain's runtime (unlike the sparse-matrix ops this test
    # complements) scales with edge count, not just node count, so this is
    # sized to prove there's no dense blowup at 40,000 nodes while keeping
    # the test itself fast.
    for _ in range(30_000):
        a, b = rng.sample(range(n_tags), 2)
        if a > b:
            a, b = b, a
        rows.append({"tag_a": tags[a], "tag_b": tags[b], "joint_count": 2,
                      "count_a": 5, "count_b": 5, "lift": 2.0, "pmi": 1.0})

    pair_stats = analysis.pd.DataFrame(rows)
    keep_tags = set(tags)

    start = time.time()
    graph = analysis.build_cluster_graph(pair_stats, keep_tags)
    communities = analysis.detect_communities(graph)
    elapsed = time.time() - start
    check("large-scale (40,000-tag) community detection completes in under 60s",
          elapsed < 60, f"took {elapsed:.2f}s")

    check("large-scale graph has a node for every tag",
          len(graph.nodes) == n_tags, f"got {len(graph.nodes)}")

    # The planted cliques should each be (majority-overlapping with) some
    # detected community -- Louvain may pull in a few noise-connected
    # stragglers, but the clique itself should mostly land together.
    for name, clique in [("A", clique_a), ("B", clique_b)]:
        clique_set = set(clique)
        best_overlap = max(len(clique_set & set(c)) for c in communities)
        check(f"planted clique {name} (30 tags) is mostly recovered as one community",
              best_overlap >= 25, f"best overlap: {best_overlap}/30")

    # Downstream of community detection at the same scale: the meta graph
    # (vectorized map + groupby over ~200,000 pair rows) and the Gephi GEXF
    # export (pure-Python XML over 40,000 nodes / ~200,000 edges) must both
    # stay tractable -- these run inside the same --all-tags invocation.
    clusters_df = analysis.assign_cluster_ids(communities)
    fandom_summary = analysis.pd.DataFrame({
        "cluster_id": sorted(clusters_df["cluster_id"].unique()),
        "n_tags": clusters_df.groupby("cluster_id")["tag_id"].nunique().values,
        "n_works": 10,
        "top_fandoms": "Fandom (100%)",
    })
    start = time.time()
    meta = analysis.build_cluster_meta_graph(pair_stats, clusters_df, fandom_summary)
    elapsed = time.time() - start
    check("large-scale meta-graph build completes in under 30s",
          elapsed < 30, f"took {elapsed:.2f}s")
    check("large-scale meta graph has one node per community",
          meta.number_of_nodes() == clusters_df["cluster_id"].nunique(),
          f"got {meta.number_of_nodes()} vs {clusters_df['cluster_id'].nunique()}")

    import tempfile as _tempfile
    with _tempfile.TemporaryDirectory() as gexf_dir:
        gexf_path = os.path.join(gexf_dir, "large.gexf")
        start = time.time()
        analysis.write_gexf_export(graph, clusters_df, gexf_path)
        elapsed = time.time() - start
        check("large-scale (40,000-node) GEXF export completes in under 120s",
              elapsed < 120, f"took {elapsed:.2f}s")
        check("large-scale GEXF file is non-empty",
              os.path.getsize(gexf_path) > 0)


def run_large_scale_merge_checks():
    # Regression test for a real-world stall: merge_small_communities used
    # to rescan every remaining community on every merge (an O(num
    # communities) scan repeated per node/neighbor, plus a second one for
    # the fully-isolated-source fallback), which is quadratic in the number
    # of communities. --all-tags runs plausibly produce many small/isolated
    # communities (noise tags with zero or one weak co-occurrence), and
    # this was confirmed directly to take 46.9s at just 12,005 communities,
    # extrapolating to an effectively unbounded stall at real (tens of
    # thousands of tags) scale -- exactly what a real --all-tags
    # --min-cluster-size 3 run hit. Reproduces the same two shapes that
    # exposed it: many small communities weakly connected to a few large
    # "hub" communities, and a worst case of thousands of fully isolated
    # singleton communities (hitting the largest-remaining-community
    # fallback path specifically).
    import networkx as nx
    import time

    def hub_and_noise_graph(n_singletons):
        graph = nx.Graph()
        n_hub_communities = 5
        hub_size = 50
        communities = []
        for i in range(n_singletons):
            node = f"tag::S{i}"
            graph.add_node(node)
            communities.append({node})
        for h in range(n_hub_communities):
            nodes = [f"tag::H{h}_{j}" for j in range(hub_size)]
            graph.add_nodes_from(nodes)
            for a in range(len(nodes)):
                for b in range(a + 1, len(nodes)):
                    graph.add_edge(nodes[a], nodes[b], weight=1.0)
            communities.append(set(nodes))
            for i in range(200):
                s = f"tag::S{(h * 200 + i) % n_singletons}"
                graph.add_edge(s, nodes[0], weight=0.1)
        return graph, communities

    graph, communities = hub_and_noise_graph(30_000)
    start = time.time()
    merged = analysis.merge_small_communities(communities, graph, min_cluster_size=3)
    elapsed = time.time() - start
    check("large-scale (30,005 communities, hub+noise) merge completes in under 10s",
          elapsed < 10, f"took {elapsed:.2f}s")
    check("every resulting community meets the minimum size (hub+noise)",
          all(len(c) >= 3 for c in merged), f"sizes: {sorted(len(c) for c in merged)}")
    total_tags = sum(len(c) for c in communities)
    check("no tag is lost merging the hub+noise graph",
          sum(len(c) for c in merged) == total_tags,
          f"expected {total_tags}, got {sum(len(c) for c in merged)}")

    # Worst case for the isolated-source fallback: thousands of fully
    # isolated singleton communities (zero edges at all -- e.g. a tag whose
    # only pairs were filtered out or had pmi <= 0 everywhere), plus a
    # couple of real communities to merge into.
    n_isolated = 20_000
    isolated_graph = nx.Graph()
    isolated_communities = []
    for i in range(n_isolated):
        node = f"tag::Iso{i}"
        isolated_graph.add_node(node)
        isolated_communities.append({node})
    big = [f"tag::Big{i}" for i in range(10)]
    isolated_graph.add_nodes_from(big)
    for a in range(len(big)):
        for b in range(a + 1, len(big)):
            isolated_graph.add_edge(big[a], big[b], weight=1.0)
    isolated_communities.append(set(big))

    start = time.time()
    merged_isolated = analysis.merge_small_communities(isolated_communities, isolated_graph,
                                                         min_cluster_size=3)
    elapsed = time.time() - start
    check("large-scale (20,001 fully-isolated communities) merge completes in under 10s",
          elapsed < 10, f"took {elapsed:.2f}s")
    check("fully-isolated communities all collapse into the one real community",
          len(merged_isolated) == 1, f"got {len(merged_isolated)} communities")


def run_meta_graph_checks():
    # Direct unit test of build_cluster_meta_graph against hand-built
    # pair_stats/clusters_df/fandom_summary -- the CSV-fixture path above
    # can't produce cross-cluster positive-PMI pairs (its two blocks never
    # co-occur), so cross-cluster aggregation is exercised here. Three
    # clusters: 1-2 share three positive-PMI pairs (and the pair rows are
    # written in both (a from 1) and (a from 2) orientations, so the
    # min/max canonicalization is what makes them aggregate together), 1-3
    # share ONE negative-PMI pair (must NOT create an edge -- affinity
    # edges only), and cluster 3 is otherwise isolated with no works.
    pair_rows = [
        # cluster 1 x cluster 2, positive pmi, both orientations
        {"tag_a": "additional_tags::A1", "tag_b": "additional_tags::B1",
         "joint_count": 4, "count_a": 8, "count_b": 8, "lift": 4.0, "pmi": 2.0},
        {"tag_a": "additional_tags::B2", "tag_b": "additional_tags::A2",
         "joint_count": 2, "count_a": 8, "count_b": 8, "lift": 2.0, "pmi": 1.0},
        {"tag_a": "additional_tags::A1", "tag_b": "additional_tags::B2",
         "joint_count": 3, "count_a": 8, "count_b": 8, "lift": 8.0, "pmi": 3.0},
        # cluster 1 x cluster 3, NEGATIVE pmi -- excluded
        {"tag_a": "additional_tags::A1", "tag_b": "additional_tags::C1",
         "joint_count": 2, "count_a": 8, "count_b": 8, "lift": 0.5, "pmi": -1.0},
        # within-cluster pair -- not a cross edge
        {"tag_a": "additional_tags::A1", "tag_b": "additional_tags::A2",
         "joint_count": 5, "count_a": 8, "count_b": 8, "lift": 4.0, "pmi": 2.0},
    ]
    pair_stats = analysis.pd.DataFrame(pair_rows)
    clusters_df = analysis.pd.DataFrame([
        {"tag_id": "additional_tags::A1", "field": "additional_tags", "label": "A1", "cluster_id": 1},
        {"tag_id": "additional_tags::A2", "field": "additional_tags", "label": "A2", "cluster_id": 1},
        {"tag_id": "additional_tags::B1", "field": "additional_tags", "label": "B1", "cluster_id": 2},
        {"tag_id": "additional_tags::B2", "field": "additional_tags", "label": "B2", "cluster_id": 2},
        {"tag_id": "additional_tags::C1", "field": "additional_tags", "label": "C1", "cluster_id": 3},
    ])
    fandom_summary = analysis.pd.DataFrame([
        {"cluster_id": 1, "n_tags": 2, "n_works": 10, "top_fandoms": "Fandom X (80%), Fandom Y (20%)"},
        {"cluster_id": 2, "n_tags": 2, "n_works": 8, "top_fandoms": "Fandom Z (100%)"},
        {"cluster_id": 3, "n_tags": 1, "n_works": 0, "top_fandoms": ""},
    ])

    meta = analysis.build_cluster_meta_graph(pair_stats, clusters_df, fandom_summary)

    check("meta graph has one node per cluster, namespaced cluster::{id}",
          set(meta.nodes) == {"cluster::1", "cluster::2", "cluster::3"},
          f"got {set(meta.nodes)}")
    check("meta node label is '{id}: {top fandom name}' (name parsed from the summary string)",
          meta.nodes["cluster::1"]["label"] == "1: Fandom X",
          f"got {meta.nodes['cluster::1']['label']!r}")
    check("a cluster with no fandom label gets the bare 'Cluster {id}' label",
          meta.nodes["cluster::3"]["label"] == "Cluster 3",
          f"got {meta.nodes['cluster::3']['label']!r}")
    check("meta node title carries n_tags/n_works/top_fandoms",
          "2 tags" in meta.nodes["cluster::1"]["title"]
          and "10 works" in meta.nodes["cluster::1"]["title"]
          and "Fandom X (80%)" in meta.nodes["cluster::1"]["title"],
          f"got {meta.nodes['cluster::1']['title']!r}")

    check("clusters 1-2 get exactly one edge aggregating both pair orientations",
          meta.number_of_edges() == 1 and meta.has_edge("cluster::1", "cluster::2"),
          f"got {list(meta.edges)}")
    edge = meta.edges["cluster::1", "cluster::2"]
    check("cross-cluster edge counts all three positive-PMI links",
          edge["n_links"] == 3, f"got {edge}")
    check("cross-cluster edge mean_pmi averages the crossing pairs' PMI",
          abs(edge["mean_pmi"] - 2.0) < 1e-9, f"got {edge}")
    check("a negative-PMI cross pair does NOT create an edge (affinity edges only)",
          not meta.has_edge("cluster::1", "cluster::3"), f"got {list(meta.edges)}")

    # Width cap: a synthetic 100,000-link edge must stay at width 10, not
    # draw a 100,000px line.
    big_pair_stats = analysis.pd.DataFrame([
        {"tag_a": f"additional_tags::A{i}", "tag_b": f"additional_tags::B{i}",
         "joint_count": 2, "count_a": 4, "count_b": 4, "lift": 2.0, "pmi": 1.0}
        for i in range(100)
    ])
    big_clusters = analysis.pd.DataFrame(
        [{"tag_id": f"additional_tags::A{i}", "field": "additional_tags",
          "label": f"A{i}", "cluster_id": 1} for i in range(100)]
        + [{"tag_id": f"additional_tags::B{i}", "field": "additional_tags",
            "label": f"B{i}", "cluster_id": 2} for i in range(100)])
    big_summary = analysis.pd.DataFrame([
        {"cluster_id": 1, "n_tags": 100, "n_works": 50, "top_fandoms": "F (100%)"},
        {"cluster_id": 2, "n_tags": 100, "n_works": 50, "top_fandoms": "G (100%)"},
    ])
    big_meta = analysis.build_cluster_meta_graph(big_pair_stats, big_clusters, big_summary)
    big_edge = big_meta.edges["cluster::1", "cluster::2"]
    check("edge visual width is log-scaled and capped (100 links -> ~5.6px, not 100px)",
          big_edge["n_links"] == 100 and big_edge["width"] <= 10,
          f"got {big_edge}")
    check("meta node size is capped so a giant cluster can't swamp the canvas",
          all(data["size"] <= 60 for _, data in big_meta.nodes(data=True)),
          f"got {[data['size'] for _, data in big_meta.nodes(data=True)]}")


def run_cluster_layout_checks():
    # Correctness: two clusters, far enough apart in the grid that their
    # circles can't overlap (compute_cluster_layout's own invariant), each
    # cluster's own nodes should stay close to their shared centroid.
    import networkx as nx

    graph = nx.Graph()
    cluster_a = ["a1", "a2", "a3"]
    cluster_b = ["b1", "b2"]
    for tag in cluster_a + cluster_b:
        graph.add_node(tag)
    graph.add_edge("a1", "a2", weight=1.0)
    graph.add_edge("a2", "a3", weight=1.0)
    graph.add_edge("b1", "b2", weight=1.0)

    clusters_df = analysis.pd.DataFrame(
        [{"tag_id": t, "field": "x", "label": t, "cluster_id": 1} for t in cluster_a]
        + [{"tag_id": t, "field": "x", "label": t, "cluster_id": 2} for t in cluster_b]
    )

    positions = analysis.compute_cluster_layout(graph, clusters_df)
    check("compute_cluster_layout returns a position for every tag",
          set(positions.keys()) == set(cluster_a + cluster_b), f"got {positions}")

    def centroid(tags):
        xs = [positions[t][0] for t in tags]
        ys = [positions[t][1] for t in tags]
        return sum(xs) / len(xs), sum(ys) / len(ys)

    def max_dist_from(center, tags):
        cx, cy = center
        return max(((positions[t][0] - cx) ** 2 + (positions[t][1] - cy) ** 2) ** 0.5 for t in tags)

    centroid_a, centroid_b = centroid(cluster_a), centroid(cluster_b)
    radius_a = max_dist_from(centroid_a, cluster_a)
    radius_b = max_dist_from(centroid_b, cluster_b)
    centroid_dist = ((centroid_a[0] - centroid_b[0]) ** 2 + (centroid_a[1] - centroid_b[1]) ** 2) ** 0.5
    check("the two clusters' bounding circles don't overlap",
          centroid_dist > radius_a + radius_b,
          f"centroid_dist={centroid_dist}, radius_a={radius_a}, radius_b={radius_b}")

    # A size-1 cluster still gets a position (no division-by-zero/empty-set
    # edge case in the single-node circular_layout path).
    single_graph = nx.Graph()
    single_graph.add_node("solo")
    single_clusters_df = analysis.pd.DataFrame(
        [{"tag_id": "solo", "field": "x", "label": "solo", "cluster_id": 1}])
    single_positions = analysis.compute_cluster_layout(single_graph, single_clusters_df)
    check("a single-node, single-cluster graph still gets a position",
          "solo" in single_positions, f"got {single_positions}")


def run_large_scale_cluster_layout_checks():
    # Regression test for a real stall: compute_cluster_layout originally
    # used nx.spring_layout per cluster, whose cost grows worse than
    # linearly (confirmed directly: 500 nodes -> 2.4s, 4000 nodes -> 44.3s)
    # -- and Louvain can plausibly collapse a large/weakly-structured graph
    # into a handful of large communities (confirmed directly on a
    # 40,000-tag/200,000-edge graph: communities of 5517, 5271, 5223, 5206,
    # 4414 nodes each), which made the *whole* layout step's cost
    # effectively unbounded (368.6s measured on exactly that shape).
    # nx.circular_layout is O(1) per node with no iteration -- this
    # reproduces the same "one dominant giant cluster" shape directly and
    # confirms the fix holds.
    import networkx as nx
    import time

    graph = nx.Graph()
    giant_cluster = [f"tag::G{i}" for i in range(15000)]
    graph.add_nodes_from(giant_cluster)
    small_clusters = []
    for c in range(20):
        members = [f"tag::S{c}_{i}" for i in range(10)]
        graph.add_nodes_from(members)
        for a in range(len(members)):
            for b in range(a + 1, len(members)):
                graph.add_edge(members[a], members[b], weight=1.0)
        small_clusters.append(members)

    rows = [{"tag_id": t, "field": "x", "label": t, "cluster_id": 1} for t in giant_cluster]
    for cluster_id, members in enumerate(small_clusters, start=2):
        rows.extend({"tag_id": t, "field": "x", "label": t, "cluster_id": cluster_id} for t in members)
    clusters_df = analysis.pd.DataFrame(rows)

    start = time.time()
    positions = analysis.compute_cluster_layout(graph, clusters_df)
    elapsed = time.time() - start
    check("compute_cluster_layout completes in under 10s with one 15,000-node cluster "
          "(nx.spring_layout would take minutes on a single cluster this size)",
          elapsed < 10, f"took {elapsed:.2f}s")
    check("every tag in the giant-cluster fixture gets a position",
          len(positions) == len(clusters_df), f"got {len(positions)} of {len(clusters_df)}")

    # Regression: layout compactness under the realistic Louvain shape (one
    # giant community + thousands of tiny ones). An earlier uniform-grid
    # version sized EVERY cell by the largest cluster, spreading this shape
    # across an ~800,000-px canvas (confirmed on a 50,290-node/5,006-cluster
    # fixture) -- vis-network's fit() then zooms out so far every node
    # paints at ~0.02px and the rendered page looks completely blank.
    # Shelf packing keeps the span near sqrt(total cluster area): for this
    # fixture ~15,000px, where the old grid gave ~362,000px.
    compact_graph = nx.Graph()
    giant = [f"tag::G{i}" for i in range(10000)]
    compact_graph.add_nodes_from(giant)
    rows = [{"tag_id": t, "field": "x", "label": t, "cluster_id": 1} for t in giant]
    for cluster_id in range(2, 2002):
        members = [f"tag::S{cluster_id}_{i}" for i in range(4)]
        compact_graph.add_nodes_from(members)
        rows.extend({"tag_id": t, "field": "x", "label": t, "cluster_id": cluster_id}
                    for t in members)
    compact_df = analysis.pd.DataFrame(rows)
    compact_positions = analysis.compute_cluster_layout(compact_graph, compact_df)
    xs = [p[0] for p in compact_positions.values()]
    ys = [p[1] for p in compact_positions.values()]
    span = max(max(xs) - min(xs), max(ys) - min(ys))
    check("giant-plus-2,000-tiny-clusters layout stays compact (span < 25,000px; "
          "the old uniform grid spread it across ~362,000px)",
          span < 25_000, f"got span {span:,.0f}px")


def main():
    tmpdir = tempfile.mkdtemp(prefix="ao3_analysis_test_")
    script_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "ao3_tag_analysis.py")

    run_frequency_checks(tmpdir, script_path)
    run_clustering_checks(tmpdir, script_path)
    run_min_cluster_size_checks()
    run_large_scale_community_checks()
    run_large_scale_merge_checks()
    run_meta_graph_checks()
    run_cluster_layout_checks()
    run_large_scale_cluster_layout_checks()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
        sys.exit(1)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
