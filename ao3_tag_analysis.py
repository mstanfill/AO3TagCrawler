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
import heapq
import json
import math
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
    trivially finite.

    Communities are tracked in `groups`, a dict keyed by a stable id (never
    reindexed as merges happen), with two auxiliary structures that turn
    every per-merge lookup into O(1)/O(log n) instead of an O(num
    communities) rescan -- the previous list-based implementation
    (`others = communities[:i] + communities[i+1:]`, then `if neighbor in
    other` for every other community) was quadratic in the number of
    communities, confirmed directly: 46.9s at 12,005 communities and 139.9s
    for the fully-isolated-source fallback path alone at 20,001 communities
    -- --all-tags runs that produce many small/isolated communities (a tag
    with zero or one weak co-occurrence) hit exactly this, and it scales to
    an effectively unbounded stall at real (tens of thousands of tags)
    scale:
      - `node_owner`: node -> its current community id, so "which community
        is this neighbor in" is a dict lookup instead of a linear scan.
      - `group_min_tag`: cached alphabetically-smallest tag_id per
        community, updated with one min() of two cached values per merge
        instead of rescanning a (possibly huge) merged set for every
        tie-break.
    `undersized_heap`/`size_heap` are min/max-heaps (by community size) of
    `(size, min_tag, community_id)`, giving O(log n) source selection and
    O(log n) "largest remaining community" lookup respectively, instead of
    an O(num communities) rescan every iteration. Merges only ever grow a
    community, so a popped heap entry that no longer matches the
    community's current size (or whose id no longer exists in `groups`) is
    simply stale and skipped -- no eager invalidation needed."""
    groups = {i: set(c) for i, c in enumerate(communities)}
    node_owner = {}
    group_min_tag = {}
    for community_id, members in groups.items():
        group_min_tag[community_id] = min(members)
        for node in members:
            node_owner[node] = community_id

    undersized_heap = [(len(members), group_min_tag[community_id], community_id)
                        for community_id, members in groups.items()
                        if len(members) < min_cluster_size]
    heapq.heapify(undersized_heap)
    size_heap = [(-len(members), group_min_tag[community_id], community_id)
                 for community_id, members in groups.items()]
    heapq.heapify(size_heap)

    def current_largest():
        while True:
            neg_size, _, community_id = size_heap[0]
            if community_id in groups and len(groups[community_id]) == -neg_size:
                return community_id
            heapq.heappop(size_heap)

    while len(groups) > 1 and undersized_heap:
        size, _, source_id = heapq.heappop(undersized_heap)
        if source_id not in groups or len(groups[source_id]) != size or size >= min_cluster_size:
            continue  # stale entry -- merged away, or grown since it was pushed

        source = groups.pop(source_id)
        source_min_tag = group_min_tag.pop(source_id)

        # highest total edge weight to the source, tie-broken by the
        # alphabetically smallest tag_id in the candidate community
        weight_by_target = {}
        for node in source:
            for neighbor, edge_data in graph[node].items():
                target_id = node_owner.get(neighbor)
                if target_id is None or target_id == source_id or target_id not in groups:
                    continue
                weight_by_target[target_id] = weight_by_target.get(target_id, 0.0) + edge_data["weight"]

        if weight_by_target:
            best_weight = max(weight_by_target.values())
            candidates = [cid for cid, weight in weight_by_target.items() if weight == best_weight]
            target_id = min(candidates, key=lambda cid: group_min_tag[cid])
        else:
            # source is fully isolated -- fall back to the currently
            # largest remaining community (tie-break: same as above)
            target_id = current_largest()

        groups[target_id] |= source
        for node in source:
            node_owner[node] = target_id
        group_min_tag[target_id] = min(group_min_tag[target_id], source_min_tag)

        new_size = len(groups[target_id])
        if new_size < min_cluster_size:
            heapq.heappush(undersized_heap, (new_size, group_min_tag[target_id], target_id))
        heapq.heappush(size_heap, (-new_size, group_min_tag[target_id], target_id))

    return list(groups.values())


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


def compute_cluster_fandom_summary(df, clusters_df, top_n):
    """Per-cluster fandom summary (lives here rather than in
    ao3_tag_fandom_labels.py -- which re-exports it -- because the cluster
    meta-network below needs the same labels, and that module imports this
    one, so the import can't go the other way): for each cluster_id, pools
    every work containing ANY of the cluster's tags -- counted once per
    cluster no matter how many of its tags the work matches -- and ranks
    the fandoms of those works by percent of works (tie-break:
    alphabetically smallest fandom name). Same denominator semantics as
    ao3_tag_fandom_labels.py's per-tag labels: the percentage base is the
    cluster's full work pool, so fandom-less works keep sums under 100 and
    multi-fandom crossover works can push sums above 100, while every
    individual value stays <= 100. Returns a DataFrame
    [cluster_id, n_tags, n_works, n_fandoms, top_fandoms] with one row per
    cluster in clusters_df -- a cluster whose tags never appear in df keeps
    its row with n_works=0, n_fandoms=0, and an empty label rather than
    vanishing. n_fandoms is how many DISTINCT fandoms the cluster's works
    span (every fandom that appears at least once, not truncated to
    top_n)."""
    cluster_by_tag = clusters_df.drop_duplicates("tag_id").set_index("tag_id")["cluster_id"]

    tag_table = viz.build_document_tag_table(df, fields=ALL_METADATA_FIELDS)
    tag_table = tag_table[tag_table["tag_id"].isin(cluster_by_tag.index)]
    cluster_works = tag_table.assign(cluster_id=tag_table["tag_id"].map(cluster_by_tag))
    # A work with several of the cluster's tags is still one story.
    cluster_works = cluster_works[["cluster_id", "work_id"]].drop_duplicates()
    cluster_totals = cluster_works.groupby("cluster_id").size()

    # Deduped for the same reason as compute_fandom_labels: the scraper
    # emits one row per (seed tag, work).
    deduped = df.drop_duplicates(subset="work_id", keep="first")
    fandom_table = viz.explode_field(deduped, "fandom")[["work_id", "fandom"]]

    merged = cluster_works.merge(fandom_table, on="work_id")
    counts = merged.groupby(["cluster_id", "fandom"]).size().reset_index(name="count")
    counts["pct"] = counts["count"] / counts["cluster_id"].map(cluster_totals) * 100

    cluster_fandoms = counts.groupby("cluster_id")["fandom"].nunique()

    counts = counts.sort_values(["cluster_id", "count", "fandom"], ascending=[True, False, True])
    top = counts.groupby("cluster_id", sort=False).head(top_n)
    top = top.assign(entry=top["fandom"] + " (" + top["pct"].round(0).astype(int).astype(str) + "%)")
    labels = top.groupby("cluster_id", sort=False)["entry"].apply(", ".join)

    n_tags = clusters_df.groupby("cluster_id")["tag_id"].nunique()
    summary = pd.DataFrame({
        "cluster_id": n_tags.index,
        "n_tags": n_tags.values,
        "n_works": n_tags.index.map(cluster_totals).fillna(0).astype(int),
        "n_fandoms": n_tags.index.map(cluster_fandoms).fillna(0).astype(int),
        "top_fandoms": n_tags.index.map(labels).fillna(""),
    })
    # ao3_tag_clusters.csv's cluster_ids are integers, but they arrive as
    # strings when the CSV is read with dtype=str -- order numerically when
    # every id parses as a number, so 2 sorts before 10.
    numeric_order = pd.to_numeric(summary["cluster_id"], errors="coerce")
    if numeric_order.notna().all():
        summary = summary.iloc[numeric_order.argsort(kind="stable").to_numpy()]
    else:
        summary = summary.sort_values("cluster_id")
    return summary.reset_index(drop=True)


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


def compute_cluster_layout(graph, clusters_df, node_spacing=40):
    """Computes a static (x, y) position for every node instead of relying
    on vis-network's client-side physics simulation -- confirmed directly
    that plain nx.spring_layout on the WHOLE graph took 72s at just 5,000
    nodes (let alone --all-tags scale), and a global force simulation
    running in the browser is exactly what makes a real
    tens-of-thousands-of-nodes network hang or crash the tab, independent
    of file size.

    Arranges each cluster's nodes on its own circle (nx.circular_layout,
    radius scaled by sqrt(cluster size) so point density stays roughly
    comparable across differently-sized clusters) and shelf-packs the
    circles: clusters sorted by diameter descending, laid out left-to-right
    in rows of a target width of ~sqrt(total cluster area), each row as
    tall as its largest member. Each circle is inscribed in its own
    diameter-sized square cell, and rows never interleave, so no two
    clusters can overlap. Deliberately NOT a uniform grid with every cell
    sized by the LARGEST cluster: real Louvain output at --all-tags scale
    is one or a few giant communities plus thousands of tiny (3-6 tag)
    ones, and a uniform grid spread those across an ~800,000-px-wide
    canvas (confirmed directly on a 50,290-node/5,006-cluster fixture) --
    vis-network's fit() then zooms out so far that every node paints at
    ~0.02px and the page looks completely blank. Shelf packing keeps the
    span proportional to sqrt(total area) instead of
    max_diameter * sqrt(cluster count).

    Also deliberately NOT spring_layout for the per-cluster circles:
    confirmed directly that its cost grows worse than linearly (500 nodes:
    2.4s, 4000 nodes: 44.3s) and a single oversized cluster -- entirely
    possible, per the Louvain shape above -- would make the whole layout
    step's cost unbounded, the exact failure mode this function exists to
    avoid. circular_layout is a closed-form O(1)-per-node placement with
    no iteration, confirmed instant even for 40,000 nodes in one cluster.
    This also produces a more useful static layout than one global
    simulation would: clusters read as clearly separated regions, matching
    what the network's own cluster-filter checkboxes already let a viewer
    toggle.

    Returns {tag_id: (x, y)}."""
    cluster_groups = clusters_df.groupby("cluster_id")["tag_id"].apply(list)

    # (diameter_px, cluster_id, tag_ids, radius_units) -- one node_spacing
    # of padding on every side so adjacent circles never touch.
    entries = []
    for cluster_id, tag_ids in cluster_groups.items():
        radius_units = max(1.0, math.sqrt(len(tag_ids)))
        diameter_px = (2 * radius_units + 2) * node_spacing
        entries.append((diameter_px, cluster_id, tag_ids, radius_units))
    if not entries:
        return {}

    # Largest first (tie-break: cluster_id, for deterministic output);
    # target row width of ~sqrt(total area) makes the packed result come
    # out roughly square, which is what fit() displays best.
    entries.sort(key=lambda e: (-e[0], e[1]))
    total_area = sum(diameter * diameter for diameter, _, _, _ in entries)
    target_width = max(entries[0][0], math.sqrt(total_area))

    positions = {}
    shelf_x = 0.0
    shelf_y = 0.0
    shelf_height = 0.0
    for diameter, cluster_id, tag_ids, radius_units in entries:
        if shelf_x > 0 and shelf_x + diameter > target_width:
            shelf_y += shelf_height
            shelf_x = 0.0
            shelf_height = 0.0
        center_x = shelf_x + diameter / 2
        center_y = shelf_y + diameter / 2
        shelf_x += diameter
        shelf_height = max(shelf_height, diameter)

        subgraph = graph.subgraph(tag_ids)
        if len(subgraph) == 1:
            local_pos = {next(iter(subgraph)): (0.0, 0.0)}
        else:
            local_pos = viz.nx.circular_layout(subgraph, scale=radius_units)
        for tag_id, (x, y) in local_pos.items():
            positions[tag_id] = (x * node_spacing + center_x, y * node_spacing + center_y)
    return positions


def build_cluster_meta_graph(pair_stats, clusters_df, fandom_summary):
    """One node per cluster instead of one per tag -- the readable summary
    view of the same data. The full tag-level network is faithful but
    cognitively unreadable at --all-tags scale (tens of thousands of
    nodes); dozens-to-hundreds of cluster nodes, each labeled with its top
    fandom from fandom_summary (compute_cluster_fandom_summary's output),
    answer "what is this data about" at a glance.

    Nodes: id "cluster::{id}" (the "::" namespace keeps
    _inject_cluster_filter_controls reusable verbatim -- its tag picker
    shows the fandom label with "(cluster)" as the field); label
    "{id}: {top fandom}" (bare "Cluster {id}" when the cluster has no
    fandom label); size scaled by sqrt(n_tags), capped so a giant cluster
    can't swamp the canvas; color/group by cluster_id, same palette as the
    tag-level network.

    Edges: one per cluster pair connected by at least one positive-PMI
    tag pair (same affinity-edges-only rationale as build_cluster_graph),
    aggregated vectorized -- n_links (how many tag pairs cross), mean_pmi
    (their average affinity). Visual width is log-scaled and capped:
    thousands of crossing links must not draw a thousand-pixel line
    (_fast_populate_network only derives width from weight when width is
    absent, so setting width explicitly takes precedence)."""
    summary_by_id = fandom_summary.set_index("cluster_id")

    graph = viz.nx.Graph()
    for cluster_id, row in summary_by_id.iterrows():
        top_fandom = row["top_fandoms"].split(" (")[0] if row["top_fandoms"] else ""
        label = f"{cluster_id}: {top_fandom}" if top_fandom else f"Cluster {cluster_id}"
        graph.add_node(
            f"cluster::{cluster_id}", label=label, group=str(cluster_id),
            color=_CLUSTER_PALETTE[(int(cluster_id) - 1) % len(_CLUSTER_PALETTE)],
            size=int(min(60, 10 + 2 * math.sqrt(row["n_tags"]))),
            title=(f"Cluster {cluster_id} — {row['n_tags']} tags, "
                   f"{row['n_works']} works — {row['top_fandoms'] or 'no fandom co-occurrence'}"),
        )

    cluster_by_tag = clusters_df.drop_duplicates("tag_id").set_index("tag_id")["cluster_id"]
    positive = pair_stats[pair_stats["pmi"] > 0]
    cluster_a = positive["tag_a"].map(cluster_by_tag)
    cluster_b = positive["tag_b"].map(cluster_by_tag)
    crossing = positive.assign(cluster_a=cluster_a, cluster_b=cluster_b)
    crossing = crossing[crossing["cluster_a"].notna() & crossing["cluster_b"].notna()
                         & (crossing["cluster_a"] != crossing["cluster_b"])]
    # Canonicalize so (3, 7) and (7, 3) aggregate together -- cluster ids
    # are ints from assign_cluster_ids, so min/max is well-defined.
    lo = crossing[["cluster_a", "cluster_b"]].min(axis=1)
    hi = crossing[["cluster_a", "cluster_b"]].max(axis=1)
    crossing = crossing.assign(cluster_lo=lo, cluster_hi=hi)
    grouped = crossing.groupby(["cluster_lo", "cluster_hi"]).agg(
        n_links=("pmi", "size"), mean_pmi=("pmi", "mean"))

    for (cluster_lo, cluster_hi), row in grouped.iterrows():
        graph.add_edge(
            f"cluster::{cluster_lo}", f"cluster::{cluster_hi}",
            n_links=int(row["n_links"]), mean_pmi=float(row["mean_pmi"]),
            width=min(10.0, 1 + math.log1p(row["n_links"])),
            title=(f"Cluster {cluster_lo} × Cluster {cluster_hi}: "
                   f"{int(row['n_links'])} positive-PMI tag pairs, "
                   f"mean pmi={row['mean_pmi']:.2f}"),
        )
    return graph


def write_gexf_export(graph, clusters_df, out_path):
    """Writes the full tag-level cluster graph as GEXF for Gephi -- the
    right tool for actually exploring a graph this size (real multithreaded
    ForceAtlas2 layout, interactive degree/weight filters, label
    management). Exports a cleaned copy: label/field/cluster_id per node
    and pmi/lift/joint_count (weight=pmi) per edge, dropping the
    pyvis-specific color/title/x/y attributes -- Gephi's own workflow
    (Appearance -> Partition by cluster_id) handles coloring, so exporting
    render hints would just be noise."""
    cluster_by_tag = clusters_df.drop_duplicates("tag_id").set_index("tag_id")["cluster_id"]

    export = viz.nx.Graph()
    for tag_id in graph.nodes():
        field, _, label = tag_id.partition("::")
        export.add_node(tag_id, label=label, field=field,
                         cluster_id=int(cluster_by_tag.get(tag_id, 0)))
    for tag_a, tag_b, data in graph.edges(data=True):
        export.add_edge(tag_a, tag_b, weight=float(data["pmi"]),
                         lift=float(data["lift"]), joint_count=int(data["joint_count"]))
    viz.nx.write_gexf(export, out_path)
    print(f"  wrote {out_path} ({export.number_of_nodes()} nodes, "
          f"{export.number_of_edges()} edges)")


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
    parser.add_argument("--cluster-meta-network-out",
                         default="ao3_tag_cluster_meta_network.html",
                         help="Cluster meta-network HTML output -- one node per "
                              "cluster, labeled with its top fandom "
                              "(default: ao3_tag_cluster_meta_network.html)")
    parser.add_argument("--gexf-out", default=None,
                         help="Also write the full tag-level cluster graph as GEXF "
                              "for Gephi (label/field/cluster_id per node, "
                              "pmi/lift/joint_count per edge). Off by default -- "
                              "the XML is large and slow to write at --all-tags "
                              "scale (default: not written)")
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
            positions = compute_cluster_layout(cluster_graph, clusters_df)
            for tag_id, (x, y) in positions.items():
                cluster_graph.nodes[tag_id]["x"] = x
                cluster_graph.nodes[tag_id]["y"] = y
            viz.render_network(cluster_graph, args.cluster_network_out,
                                inject_filters=_inject_cluster_filter_controls, physics=False)

            fandom_summary = compute_cluster_fandom_summary(df, clusters_df, top_n=3)
            meta_graph = build_cluster_meta_graph(pair_stats, clusters_df, fandom_summary)
            viz.render_network(meta_graph, args.cluster_meta_network_out,
                                inject_filters=_inject_cluster_filter_controls)

            if args.gexf_out:
                write_gexf_export(cluster_graph, clusters_df, args.gexf_out)


if __name__ == "__main__":
    main()
