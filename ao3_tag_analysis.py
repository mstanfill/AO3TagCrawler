#!/usr/bin/env python3
"""Two further analyses over ao3_tag_metadata.csv, beyond ao3_tag_visualizer.py.

  - additional_tags frequency ranking: which values are most common, and
    which are least common (excluding one-off singletons)
  - cross-field community detection: pools labels from ALL metadata fields
    (rating, warnings, category, fandom, relationship, character,
    additional_tags -- a superset of ao3_tag_visualizer.py's --tag-pairs
    four-field pool) and groups them by lift/PMI similarity using
    graph-based community detection (networkx's Louvain), rendered as an
    interactive network graph plus a discrete cluster-membership CSV.

Reuses ao3_tag_visualizer.py's tag-pair-statistics machinery unmodified via
import (its main() is guarded, so importing it has no side effects). No
network access is required -- this only reads a local CSV.
"""
import argparse
import json
import sys

import pandas as pd
from networkx.algorithms.community import louvain_communities

import ao3_tag_visualizer as viz

ALL_METADATA_FIELDS = ["rating", "warnings", "category", "fandom",
                        "relationship", "character", "additional_tags"]

# Fixed seed for Louvain's internal tie-breaking -- not user-exposed, exists
# purely so the same input always produces the same partition (matches this
# codebase's existing preference for deterministic output, e.g. the
# alphabetical tie-breaks throughout ao3_tag_visualizer.py).
_CLUSTER_SEED = 0

# Small fixed qualitative palette, cycled by cluster_id -- unlike
# FIELD_COLORS (one fixed color per metadata field), the number of clusters
# is data-dependent and unbounded, so this is indexed by cluster_id % len(),
# not looked up by name.
_CLUSTER_PALETTE = [
    "#4C72B0", "#DD8452", "#55A868", "#C44E52", "#8172B2",
    "#937860", "#CCB974", "#64B5CD", "#8C8C8C", "#E377C2",
]


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
# Cross-field community detection (lift/PMI, networkx Louvain)
#
# A third question, beyond ao3_tag_visualizer.py's two: not "what does a
# seed tag associate with" or "which folksonomy tags co-occur", but "which
# labels -- of ANY metadata field, rating/warnings/category included --
# tend to appear together", grouped via graph-based community detection
# rather than ranked pairwise. Reuses ao3_tag_visualizer.py's
# tag-pair-statistics machinery (build_document_tag_table,
# top_k_tags_by_document_frequency, build_tag_incidence_matrix,
# tag_pair_statistics, apply_min_pair_count) unmodified -- all of it is
# already field-count-agnostic, just called here with ALL_METADATA_FIELDS
# instead of TAG_PAIR_FIELDS. apply_pmi_thresholds and tag_pair_matrix are
# deliberately NOT reused: this operates on the sparse edge list directly,
# never materializing a dense tag x tag matrix -- a dense (or even
# condensed-pairwise-distance) representation is infeasible at real AO3
# scale (tens of thousands of tags under --all-tags; a dense matrix alone
# needs tens of gigabytes, which is exactly what motivated this rewrite).
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


def build_cluster_graph(pair_stats, keep_tags):
    """One-mode graph over every tag in keep_tags -- every tag becomes a
    node upfront (label/field via "::" split, same pattern
    viz.build_tag_pair_graph uses), so a tag with zero surviving pairs
    still appears as an isolated node, mirroring tag_pair_matrix's
    "Found Family stays as an all-NaN row" precedent (deliberately
    different from viz.build_tag_pair_graph, which only adds nodes that
    appear in a pair_stats row). Edges are added only for pmi > 0 pairs
    (weight=pmi) -- a "community of co-occurring tags" should only be
    built from affinity edges; pmi is the de-biased, bounded statistic for
    "co-occurs more than chance" (unlike joint_count, which conflates
    association with individual popularity, or lift, which is unbounded
    and skewed by rare noisy pairs -- the same reasoning
    apply_min_pair_count already exists to guard against)."""
    graph = viz.nx.Graph()
    for tag_id in keep_tags:
        field, _, label = tag_id.partition("::")
        graph.add_node(tag_id, label=label, group=field,
                        color=viz.FIELD_COLORS[field], title=tag_id)
    # itertuples(), not iterrows(): at real --all-tags scale (hundreds of
    # thousands of surviving pairs), iterrows()'s per-row Series
    # construction is a real bottleneck -- itertuples() is a drop-in,
    # substantially faster replacement.
    for row in pair_stats[pair_stats["pmi"] > 0].itertuples(index=False):
        graph.add_edge(
            row.tag_a, row.tag_b, weight=float(row.pmi),
            pmi=float(row.pmi), lift=float(row.lift),
            joint_count=int(row.joint_count),
            title=(f"{row.tag_a} × {row.tag_b}: more likely than chance "
                   f"(lift={row.lift:.2f}, pmi={row.pmi:.2f}, "
                   f"joint count={int(row.joint_count)})"),
        )
    return graph


def detect_communities(graph, resolution=1.0):
    """Thin wrapper over networkx's Louvain community detection, operating
    directly on the sparse graph -- only real edges are ever considered,
    no dense tag x tag structure at any point, so this scales to tens of
    thousands of tags the way hierarchical clustering (pdist + linkage)
    never could. resolution controls community granularity: higher =
    more/smaller communities, lower = fewer/larger (1.0 is networkx's own
    default -- unbiased standard modularity). A fixed seed makes the
    partition deterministic given the same graph. Returns a list of
    frozensets of tag_ids; handles an empty graph ([]) and isolated nodes
    (their own singleton community) with no special-casing needed --
    that's Louvain's native behavior, confirmed directly."""
    return louvain_communities(graph, weight="weight", resolution=resolution,
                                seed=_CLUSTER_SEED)


def merge_small_communities(communities, graph, min_cluster_size):
    """Merges communities smaller than min_cluster_size into another
    community, rather than cutting to a fixed count (there's no
    Louvain-native "ceiling" the way fcluster had n_clusters) -- repeat:
    among undersized communities, pick the smallest (tie-break:
    alphabetically smallest tag_id) as the merge source; sum graph edge
    weights from the source to every other community and merge into
    whichever has the highest total (tie-break: alphabetically smallest
    tag_id). A source with zero edges to anything else (fully isolated)
    merges into the currently-largest remaining community instead (same
    tie-break). Terminates when nothing is undersized or only one
    community remains -- never drops a tag, matching the old ceiling
    design's "always terminates, never silently loses data" spirit. Each
    iteration strictly reduces the community count by one, so this is
    trivially finite."""
    communities = [set(c) for c in communities]
    while len(communities) > 1:
        undersized = [c for c in communities if len(c) < min_cluster_size]
        if not undersized:
            break
        source = min(undersized, key=lambda c: (len(c), min(c)))
        source_idx = communities.index(source)
        others = communities[:source_idx] + communities[source_idx + 1:]

        weights = [0.0] * len(others)
        for node in source:
            for neighbor in graph.neighbors(node):
                for i, other in enumerate(others):
                    if neighbor in other:
                        weights[i] += graph[node][neighbor]["weight"]
                        break

        if max(weights, default=0.0) > 0.0:
            # highest total edge weight to the source, tie-broken by the
            # alphabetically smallest tag_id in the candidate community
            best_weight = max(weights)
            candidates = [i for i in range(len(others)) if weights[i] == best_weight]
            best_idx = min(candidates, key=lambda i: min(others[i]))
        else:
            # source is fully isolated -- fall back to the currently
            # largest remaining community (tie-break: same as above)
            best_idx = min(range(len(others)), key=lambda i: (-len(others[i]), min(others[i])))

        others[best_idx] |= source
        communities = others
    return communities


def assign_cluster_ids(communities):
    """Sorts communities by (-size, alphabetically-smallest tag_id) for
    deterministic numbering, assigns cluster_id 1..k. Returns a DataFrame
    [tag_id, field, label, cluster_id] sorted by (cluster_id, tag_id) --
    identical output contract to the old hierarchical-clustering version.
    field/label are recovered by splitting tag_id on "::", the same trick
    viz.build_tag_pair_graph already uses."""
    ordered = sorted(communities, key=lambda c: (-len(c), min(c) if c else ""))
    rows = []
    for cluster_id, community in enumerate(ordered, start=1):
        for tag_id in community:
            field, _, label = tag_id.partition("::")
            rows.append({"tag_id": tag_id, "field": field, "label": label,
                          "cluster_id": cluster_id})
    result = pd.DataFrame(rows, columns=["tag_id", "field", "label", "cluster_id"])
    return result.sort_values(["cluster_id", "tag_id"]).reset_index(drop=True)


def color_graph_by_cluster(graph, clusters_df):
    """Mutates graph in place: recolors/regroups every node by its final
    cluster_id (instead of by field) and enriches its hover title, then
    returns the same graph for chaining into render_network."""
    cluster_by_tag = clusters_df.set_index("tag_id")["cluster_id"]
    for tag_id, cluster_id in cluster_by_tag.items():
        node = graph.nodes[tag_id]
        node["group"] = str(cluster_id)
        node["color"] = _CLUSTER_PALETTE[(cluster_id - 1) % len(_CLUSTER_PALETTE)]
        node["title"] = f"{tag_id} (cluster {cluster_id})"
    return graph


def _all_cluster_tags(graph):
    """Like viz._all_tags_from_graph, but "field" is recovered from the
    tag_id's own "::" namespace rather than the node's group attribute --
    after color_graph_by_cluster, group holds the cluster_id (needed by
    the reused filter-panel JS below, which filters by checking each
    node's group against the checked checkboxes -- here, one checkbox per
    cluster rather than per field), not the original metadata field, so
    the tag picker would otherwise show "(3)" instead of "(rating)"."""
    return [{"id": node_id, "label": data["label"], "field": node_id.partition("::")[0]}
            for node_id, data in graph.nodes(data=True)]


def _cluster_filter_controls_html(graph):
    """Same shape as viz._tag_pair_filter_controls_html, for the
    cluster-colored one-mode graph: checkboxes iterate the sorted set of
    cluster_ids actually present in the graph (there's no fixed field list
    the way TAG_PAIR_FIELDS is) instead of a fixed field list, and the tag
    picker's underlying data is [{"id","label","field"}, ...] exactly like
    the tag-pair picker (label alone can still collide across fields)."""
    all_tags = _all_cluster_tags(graph)
    all_tags_json = json.dumps(all_tags).replace("</script", "<\\/script")

    cluster_ids = sorted({data["group"] for _, data in graph.nodes(data=True)}, key=int)
    checkbox_items = "\n".join(
        '<label class="ao3-cat-label">'
        f'<input type="checkbox" class="ao3-cat-checkbox" data-group="{cluster_id}" checked>'
        f'<span class="ao3-swatch" style="background-color:'
        f'{_CLUSTER_PALETTE[(int(cluster_id) - 1) % len(_CLUSTER_PALETTE)]};"></span>'
        f'Cluster {cluster_id}</label>'
        for cluster_id in cluster_ids
    )
    panel_html = viz._TAG_PAIR_FILTER_PANEL_HTML.replace("__CHECKBOX_ITEMS__", checkbox_items)
    script_html = viz._TAG_PAIR_FILTER_SCRIPT_TEMPLATE.replace("__ALL_TAGS_JSON__", all_tags_json)
    return panel_html, script_html


def _inject_cluster_filter_controls(html_path, graph):
    with open(html_path, encoding="utf-8") as f:
        html = f.read()
    assert html.count("<body>") == 1, "expected exactly one <body> tag"
    assert html.count("</body>") == 1, "expected exactly one </body> tag"
    panel_html, script_html = _cluster_filter_controls_html(graph)
    html = html.replace("<body>", "<body>" + panel_html, 1)
    html = html.replace("</body>", script_html + "</body>", 1)
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_arg_parser():
    parser = argparse.ArgumentParser(
        description="Frequency ranking and cross-field community detection "
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
                              "fields, by document frequency, before clustering. "
                              "Overridden by --all-tags (default: 60)")
    parser.add_argument("--all-tags", action="store_true",
                         help="Cluster using every tag from all 7 metadata fields, "
                              "ignoring --top-tags (default: off)")
    parser.add_argument("--min-pair-count", type=int, default=2,
                         help="Drop pairs co-occurring fewer than this many times "
                              "before clustering -- lift/PMI is unreliable at tiny "
                              "sample sizes (default: 2)")
    parser.add_argument("--cluster-resolution", type=float, default=1.0,
                         help="Louvain community-detection resolution -- higher means "
                              "more, smaller communities; lower means fewer, larger "
                              "ones (default: 1.0)")
    parser.add_argument("--min-cluster-size", type=int, default=1,
                         help="Merge communities smaller than this into another "
                              "community (their strongest-connected neighbor, or the "
                              "largest remaining community if fully isolated) "
                              "(default: 1, no minimum)")
    parser.add_argument("--cluster-network-out", default="ao3_tag_cluster_network.html",
                         help="Cluster network HTML output "
                              "(default: ao3_tag_cluster_network.html)")
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
        print("Building cross-field community detection")
        if args.all_tags:
            print("  --all-tags set: pooling every tag (no top-N truncation) -- "
                  "this may be slow/large on bigger datasets", file=sys.stderr)
        top_tags = None if args.all_tags else args.top_tags
        pair_stats, keep_tags = build_all_fields_pair_data(
            df, top_tags, args.min_pair_count)
        cluster_graph = build_cluster_graph(pair_stats, keep_tags)
        communities = detect_communities(cluster_graph, resolution=args.cluster_resolution)
        communities = merge_small_communities(communities, cluster_graph, args.min_cluster_size)
        clusters_df = assign_cluster_ids(communities)
        clusters_df.to_csv(args.clusters_out, index=False)
        print(f"  wrote {args.clusters_out} ({len(clusters_df)} tags, "
              f"{clusters_df['cluster_id'].nunique()} clusters)")

        if not clusters_df.empty:
            color_graph_by_cluster(cluster_graph, clusters_df)
            viz.render_network(cluster_graph, args.cluster_network_out,
                                inject_filters=_inject_cluster_filter_controls)


if __name__ == "__main__":
    main()
