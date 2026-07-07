#!/usr/bin/env python3
"""Two further analyses over ao3_tag_metadata.csv, beyond ao3_tag_visualizer.py.

  - additional_tags frequency ranking: which values are most common, and
    which are least common (excluding one-off singletons)
  - cross-field hierarchical clustering: pools labels from ALL metadata
    fields (rating, warnings, category, fandom, relationship, character,
    additional_tags -- a superset of ao3_tag_visualizer.py's --tag-pairs
    four-field pool) and clusters them by lift/PMI similarity, rendered as
    a seaborn clustermap (dendrogram + reordered heatmap) plus a discrete
    cluster-membership CSV.

Reuses ao3_tag_visualizer.py's tag-pair-statistics machinery unmodified via
import (its main() is guarded, so importing it has no side effects). No
network access is required -- this only reads a local CSV.
"""
import argparse
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import pdist

import ao3_tag_visualizer as viz

ALL_METADATA_FIELDS = ["rating", "warnings", "category", "fandom",
                        "relationship", "character", "additional_tags"]

CLUSTER_METHODS = ["average", "complete", "ward", "single"]


# ---------------------------------------------------------------------------
# additional_tags frequency ranking
# ---------------------------------------------------------------------------

def additional_tags_frequency(df, min_bottom_count=2):
    """Returns (most_frequent_seed, most_frequent_non_seed, least_frequent),
    each a DataFrame [additional_tags, count]. Seed tags are the values in
    df["tag"] (the AO3 tag actually searched to find each work, same
    definition ao3_tag_visualizer.py's rank_seed_tags uses) -- an
    additional_tags value that also happens to be a seed tag partly
    reflects the scrape's own search bias rather than a genuinely emergent
    discovery, so it's split out from non-seed values. Both "most" buckets
    are sorted highest-count first (alphabetical tie-break, matching
    top_n_values's convention). least_frequent is unchanged: drawn from the
    full additional_tags pool (seed tags included), pre-filtered to
    count >= min_bottom_count (default 2, i.e. excludes true one-off
    singletons), sorted lowest-count first (alphabetical tie-break)."""
    seed_tags = set(df["tag"].unique())
    exploded = viz.explode_field(df, "additional_tags")
    counts = viz.total_value_counts(exploded, "additional_tags")
    counts = counts.reset_index()
    counts.columns = ["additional_tags", "count"]

    def sort_desc(c):
        return c.sort_values(["count", "additional_tags"], ascending=[False, True])

    is_seed = counts["additional_tags"].isin(seed_tags)
    most_frequent_seed = sort_desc(counts[is_seed])
    most_frequent_non_seed = sort_desc(counts[~is_seed])

    least_frequent = counts[counts["count"] >= min_bottom_count]
    least_frequent = least_frequent.sort_values(["count", "additional_tags"],
                                                 ascending=[True, True])
    return most_frequent_seed, most_frequent_non_seed, least_frequent


def write_frequency_csv(most_frequent_seed, most_frequent_non_seed, least_frequent,
                         top_n, bottom_n, out_path):
    most_seed = most_frequent_seed.head(top_n).copy()
    most_seed["rank_type"] = "most_frequent_seed_tag"
    most_non_seed = most_frequent_non_seed.head(top_n).copy()
    most_non_seed["rank_type"] = "most_frequent_non_seed_tag"
    least = least_frequent.head(bottom_n).copy()
    least["rank_type"] = "least_frequent"
    combined = pd.concat([most_seed, most_non_seed, least], ignore_index=True)
    combined = combined[["rank_type", "additional_tags", "count"]]
    combined.to_csv(out_path, index=False)
    return combined


def print_frequency_summary(most_frequent_seed, most_frequent_non_seed, least_frequent,
                             top_n, bottom_n, out_path):
    print(f"  wrote {out_path} "
          f"({min(top_n, len(most_frequent_seed))} most frequent seed tags, "
          f"{min(top_n, len(most_frequent_non_seed))} most frequent non-seed tags, "
          f"{min(bottom_n, len(least_frequent))} least frequent)")


# ---------------------------------------------------------------------------
# Cross-field hierarchical clustering (lift/PMI, seaborn clustermap)
#
# A third question, beyond ao3_tag_visualizer.py's two: not "what does a
# seed tag associate with" or "which folksonomy tags co-occur", but "which
# labels -- of ANY metadata field, rating/warnings/category included --
# tend to appear together", grouped via hierarchical clustering rather than
# ranked pairwise. Reuses ao3_tag_visualizer.py's tag-pair-statistics
# machinery (build_document_tag_table, top_k_tags_by_document_frequency,
# build_tag_incidence_matrix, tag_pair_statistics, apply_min_pair_count,
# tag_pair_matrix) unmodified -- all of it is already field-count-agnostic,
# just called here with ALL_METADATA_FIELDS instead of TAG_PAIR_FIELDS.
# apply_pmi_thresholds is deliberately NOT reused: that "boring middle"
# exclusion is specific to picking out strong edges for a network view;
# clustering wants the full similarity structure, including near-zero PMI.
# ---------------------------------------------------------------------------

def build_all_fields_pair_data(df, top_tags, min_pair_count):
    """Orchestrator, analogous to viz.build_tag_pair_data but pooling all
    seven metadata fields instead of just the four folksonomy ones.
    Returns (pair_stats, keep_tags)."""
    tag_table = viz.build_document_tag_table(df, fields=ALL_METADATA_FIELDS)
    keep_tags = viz.top_k_tags_by_document_frequency(tag_table, top_tags)
    if keep_tags is None:
        keep_tags = set(tag_table["tag_id"].unique())
    incidence = viz.build_tag_incidence_matrix(tag_table, keep_tags)
    n_docs = df["work_id"].nunique()
    pair_stats = viz.tag_pair_statistics(incidence, n_docs)
    pair_stats = viz.apply_min_pair_count(pair_stats, min_pair_count)
    return pair_stats, keep_tags


def build_cluster_matrix(pair_stats, keep_tags):
    """Returns (display_matrix, fill_matrix). display_matrix is the
    symmetric tag x tag PMI matrix with NaN for missing/filtered pairs
    (viz.tag_pair_matrix's existing semantics, untouched). fill_matrix
    replaces those NaNs with 0.0 -- scipy's pdist/linkage cannot handle
    NaN, and treating "no observed co-occurrence" as "independent" is a
    deliberate simplification scoped to clustering input only; the
    displayed heatmap still masks true-NaN cells blank via display_matrix,
    so this fill never shows up as a fabricated data point."""
    display_matrix = viz.tag_pair_matrix(pair_stats, keep_tags, value_col="pmi")
    fill_matrix = display_matrix.fillna(0.0)
    return display_matrix, fill_matrix


def compute_linkage(fill_matrix, method="average"):
    """Hierarchical clustering linkage over the tags in fill_matrix (each
    tag's row is its similarity profile against every other tag). Returns
    None if there are fewer than 2 tags to cluster (mirrors
    render_heatmap's empty-input skip)."""
    if fill_matrix.shape[0] < 2:
        return None
    distances = pdist(fill_matrix.to_numpy(), metric="euclidean")
    return linkage(distances, method=method)


def cut_clusters(linkage_matrix, tags, n_clusters):
    """Cuts the dendrogram into n_clusters discrete groups via
    scipy.cluster.hierarchy.fcluster. Returns a DataFrame
    [tag_id, field, label, cluster_id] sorted by (cluster_id, tag_id).
    field/label are recovered by splitting tag_id on "::", the same trick
    viz.build_tag_pair_graph already uses."""
    cluster_ids = fcluster(linkage_matrix, t=n_clusters, criterion="maxclust")
    rows = []
    for tag_id, cluster_id in zip(tags, cluster_ids):
        field, _, label = tag_id.partition("::")
        rows.append({"tag_id": tag_id, "field": field, "label": label,
                      "cluster_id": int(cluster_id)})
    result = pd.DataFrame(rows, columns=["tag_id", "field", "label", "cluster_id"])
    return result.sort_values(["cluster_id", "tag_id"]).reset_index(drop=True)


def render_cluster_heatmap(display_matrix, fill_matrix, linkage_matrix, out_path):
    if fill_matrix.empty or linkage_matrix is None:
        print("  skipping cluster heatmap: fewer than 2 tags after filtering",
              file=sys.stderr)
        return

    width = max(10, 0.35 * fill_matrix.shape[1] + 4)
    height = max(8, 0.3 * fill_matrix.shape[0] + 4)
    annot = fill_matrix.shape[0] * fill_matrix.shape[1] <= 500

    grid = sns.clustermap(
        fill_matrix, row_linkage=linkage_matrix, col_linkage=linkage_matrix,
        mask=display_matrix.isna(), cmap="coolwarm", center=0,
        annot=annot, fmt=".2f", figsize=(width, height),
        cbar_kws={"label": "PMI (log2 lift)"},
    )
    grid.savefig(out_path, dpi=150)
    plt.close(grid.figure)
    print(f"  wrote {out_path} ({fill_matrix.shape[0]}x{fill_matrix.shape[1]})")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_arg_parser():
    parser = argparse.ArgumentParser(
        description="Frequency ranking and cross-field hierarchical clustering "
                     "over AO3 tag metadata.",
    )
    parser.add_argument("--input", default="ao3_tag_metadata.csv",
                         help="Metadata CSV to read (default: ao3_tag_metadata.csv)")

    parser.add_argument("--frequency-top-n", type=int, default=20,
                         help="Most frequent additional_tags to report, per category -- "
                              "seed tags (values that are also a searched seed tag) and "
                              "non-seed tags each get up to this many (default: 20)")
    parser.add_argument("--frequency-bottom-n", type=int, default=20,
                         help="Least frequent additional_tags to report (default: 20)")
    parser.add_argument("--frequency-min-count", type=int, default=2,
                         help="Floor for \"least frequent\" -- excludes values with "
                              "fewer works than this, e.g. the default of 2 excludes "
                              "one-off singletons (default: 2)")
    parser.add_argument("--frequency-out", default="ao3_additional_tags_frequency.csv",
                         help="Frequency ranking CSV output "
                              "(default: ao3_additional_tags_frequency.csv)")

    parser.add_argument("--top-tags", type=int, default=60,
                         help="Top N tags overall, pooled across all 7 metadata "
                              "fields, by document frequency, before clustering "
                              "(default: 60)")
    parser.add_argument("--min-pair-count", type=int, default=2,
                         help="Drop pairs co-occurring fewer than this many times "
                              "before clustering -- lift/PMI is unreliable at tiny "
                              "sample sizes (default: 2)")
    parser.add_argument("--n-clusters", type=int, default=10,
                         help="Cut the dendrogram into this many discrete clusters "
                              "(default: 10)")
    parser.add_argument("--cluster-method", choices=CLUSTER_METHODS, default="average",
                         help="scipy linkage method (default: average)")
    parser.add_argument("--heatmap-out-dir", default="heatmaps",
                         help="Directory for the cluster heatmap PNG (default: heatmaps)")
    parser.add_argument("--cluster-heatmap-out", default=None,
                         help="Cluster heatmap PNG output "
                              "(default: <--heatmap-out-dir>/heatmap_clusters.png)")
    parser.add_argument("--clusters-out", default="ao3_tag_clusters.csv",
                         help="Cluster-membership CSV output (default: ao3_tag_clusters.csv)")

    parser.add_argument("--frequency-only", action="store_true",
                         help="Only compute the frequency ranking, skip clustering")
    parser.add_argument("--clusters-only", action="store_true",
                         help="Only compute clustering, skip the frequency ranking")
    return parser


def main(argv=None):
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    df = viz.load_metadata(args.input)

    if not args.clusters_only:
        print("Building additional_tags frequency ranking")
        most_frequent_seed, most_frequent_non_seed, least_frequent = additional_tags_frequency(
            df, min_bottom_count=args.frequency_min_count)
        write_frequency_csv(most_frequent_seed, most_frequent_non_seed, least_frequent,
                             args.frequency_top_n, args.frequency_bottom_n,
                             args.frequency_out)
        print_frequency_summary(most_frequent_seed, most_frequent_non_seed, least_frequent,
                                 args.frequency_top_n, args.frequency_bottom_n,
                                 args.frequency_out)

    if not args.frequency_only:
        print("Building cross-field hierarchical clustering")
        pair_stats, keep_tags = build_all_fields_pair_data(
            df, args.top_tags, args.min_pair_count)
        display_matrix, fill_matrix = build_cluster_matrix(pair_stats, keep_tags)
        linkage_matrix = compute_linkage(fill_matrix, method=args.cluster_method)

        os.makedirs(args.heatmap_out_dir, exist_ok=True)
        heatmap_out = args.cluster_heatmap_out or os.path.join(
            args.heatmap_out_dir, "heatmap_clusters.png")
        render_cluster_heatmap(display_matrix, fill_matrix, linkage_matrix, heatmap_out)

        if linkage_matrix is not None:
            clusters_df = cut_clusters(linkage_matrix, list(fill_matrix.index),
                                        args.n_clusters)
            clusters_df.to_csv(args.clusters_out, index=False)
            print(f"  wrote {args.clusters_out} ({len(clusters_df)} tags, "
                  f"{clusters_df['cluster_id'].nunique()} clusters)")


if __name__ == "__main__":
    main()
