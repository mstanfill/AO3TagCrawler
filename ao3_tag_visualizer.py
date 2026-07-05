#!/usr/bin/env python3
"""Visualize connections between seed tags and per-work attributes.

Reads an ao3_tag_metadata.csv (produced by ao3_tag_scraper.py) and builds:

  - an interactive bipartite network graph: seed tags <-> attribute values
    (rating, warnings, category, fandom, additional_tags), edges weighted by
    co-occurrence count
  - a co-occurrence heatmap per attribute field: rows = seed tags, columns =
    attribute values, cell = co-occurrence count (or %% of that tag's works)

fandom and additional_tags are high-cardinality, so each is filtered to its
top-N most frequent values (computed from the full dataset) before either
visualization is built. No network access is required -- this only reads a
local CSV.
"""
import argparse
import os
import re
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import networkx as nx
import pandas as pd
import seaborn as sns
from pyvis.network import Network

DELIMITER = ", "

FIELDS_TO_VISUALIZE = ["rating", "warnings", "category", "fandom", "additional_tags"]
FIELDS_TOP_N_ELIGIBLE = {"fandom", "additional_tags"}

FIELD_COLORS = {
    "seed_tag": "#4C72B0",
    "rating": "#DD8452",
    "warnings": "#55A868",
    "category": "#C44E52",
    "fandom": "#8172B2",
    "additional_tags": "#937860",
}


# ---------------------------------------------------------------------------
# Loading and exploding
# ---------------------------------------------------------------------------

def load_metadata(input_csv):
    if not os.path.exists(input_csv):
        print(f"Input file not found: {input_csv}", file=sys.stderr)
        sys.exit(1)
    df = pd.read_csv(input_csv, dtype=str, keep_default_na=False)
    if df.empty:
        print(f"{input_csv} has no data rows.", file=sys.stderr)
        sys.exit(1)
    return df


def split_values(cell, delimiter=DELIMITER):
    if not cell:
        return []
    values = [v.strip() for v in cell.split(delimiter) if v.strip()]
    return list(dict.fromkeys(values))


def explode_field(df, field):
    exploded = df[["tag", "work_id", field]].copy()
    exploded[field] = exploded[field].map(split_values)
    exploded = exploded.explode(field)
    exploded = exploded[exploded[field].notna() & (exploded[field] != "")]
    return exploded


# ---------------------------------------------------------------------------
# Counting and filtering
# ---------------------------------------------------------------------------

def total_value_counts(exploded, field):
    return exploded[field].value_counts()


def top_n_values(exploded, field, n):
    if n is None:
        return None
    counts = total_value_counts(exploded, field)
    counts = counts.sort_values(ascending=False)
    # break ties alphabetically for determinism
    counts = counts.reset_index()
    counts.columns = [field, "count"]
    counts = counts.sort_values(["count", field], ascending=[False, True])
    return set(counts[field].head(n))


def cooccurrence_counts(exploded, field, keep_values):
    if keep_values is not None:
        exploded = exploded[exploded[field].isin(keep_values)]
    if exploded.empty:
        return pd.DataFrame(columns=["tag", field, "count"])
    counts = exploded.groupby(["tag", field]).size().reset_index(name="count")
    return counts


def apply_min_count(counts, min_count):
    return counts[counts["count"] >= min_count]


def rank_seed_tags(df, top_seed_tags):
    ranked = df["tag"].value_counts().index.tolist()
    if top_seed_tags is None:
        return ranked
    return ranked[:top_seed_tags]


# ---------------------------------------------------------------------------
# Heatmap matrix + rendering
# ---------------------------------------------------------------------------

def cooccurrence_matrix(counts, field, seed_tags, normalize_by=None):
    if counts.empty:
        return pd.DataFrame(index=seed_tags)
    matrix = counts.pivot(index="tag", columns=field, values="count")
    # reindex()'s fill_value only fills newly-added rows/columns -- it doesn't
    # backfill NaN cells pivot() already introduced for missing (tag, value)
    # combos among the existing rows/columns, so fillna first.
    matrix = matrix.fillna(0)
    columns = sorted(matrix.columns)
    matrix = matrix.reindex(index=seed_tags, columns=columns, fill_value=0)
    if normalize_by is not None:
        matrix = matrix.div(normalize_by.reindex(seed_tags), axis=0) * 100
    else:
        # pivot() upcasts to float64 when any (tag, value) combo is missing
        # (NaN before the reindex fill); counts are always whole numbers.
        matrix = matrix.astype(int)
    return matrix


def render_heatmap(matrix, field, out_path, normalized=False):
    if matrix.empty or matrix.shape[1] == 0:
        print(f"  skipping heatmap for {field}: no data after filtering", file=sys.stderr)
        return

    width = max(8, 0.35 * matrix.shape[1] + 3)
    height = max(6, 0.22 * matrix.shape[0] + 3)
    fig, ax = plt.subplots(figsize=(width, height))
    annot = matrix.shape[0] * matrix.shape[1] <= 500
    fmt = ".1f" if normalized else "d"
    label = "% of tag's works" if normalized else "co-occurrence count"
    sns.heatmap(matrix, annot=annot, fmt=fmt, cmap="viridis",
                cbar_kws={"label": label}, ax=ax)
    ax.set_xlabel(field)
    ax.set_ylabel("seed tag")
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    print(f"  wrote {out_path} ({matrix.shape[0]}x{matrix.shape[1]})")


# ---------------------------------------------------------------------------
# Network graph
# ---------------------------------------------------------------------------

def build_bipartite_graph(field_tables, seed_tags):
    graph = nx.Graph()
    seed_tag_set = set(seed_tags)
    for tag in seed_tags:
        graph.add_node(f"tag::{tag}", label=tag, group="seed_tag",
                        color=FIELD_COLORS["seed_tag"], title=tag)

    for field, counts in field_tables.items():
        for _, row in counts.iterrows():
            tag, value, count = row["tag"], row[field], row["count"]
            if tag not in seed_tag_set:
                continue
            value_id = f"{field}::{value}"
            if value_id not in graph:
                graph.add_node(value_id, label=value, group=field,
                                color=FIELD_COLORS[field], title=f"{field}: {value}")
            graph.add_edge(f"tag::{tag}", value_id, weight=int(count),
                            title=f"{tag} × {value}: {count} works")
    return graph


_BOOTSTRAP_TAG_RE = re.compile(
    r'<(?:link|script)[^>]*\bhttps://cdn\.jsdelivr\.net/npm/bootstrap[^>]*>(?:</script>)?\s*'
)


def _strip_bootstrap_cdn(html_path):
    # pyvis's bundled template.html unconditionally references Bootstrap from
    # a CDN for the "card"/"card-body" wrapper div, regardless of
    # cdn_resources (which only inlines vis-network's own JS/CSS). That div
    # is purely cosmetic -- the graph itself is vis-network, already inlined,
    # and has no Bootstrap dependency -- so strip the two tags to make the
    # file genuinely self-contained.
    with open(html_path, encoding="utf-8") as f:
        html = f.read()
    html = _BOOTSTRAP_TAG_RE.sub("", html)
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)


def render_network(graph, out_path, notebook=False):
    # show_buttons() would pull in its own control-panel styling the same way
    # -- omitted so the output file stays self-contained.
    net = Network(height="800px", width="100%", notebook=notebook, cdn_resources="in_line")
    net.from_nx(graph)
    net.write_html(out_path, notebook=notebook)
    _strip_bootstrap_cdn(out_path)
    print(f"  wrote {out_path} ({graph.number_of_nodes()} nodes, {graph.number_of_edges()} edges)")
    return net


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_arg_parser():
    parser = argparse.ArgumentParser(
        description="Visualize connections between seed tags and work attributes.",
    )
    parser.add_argument("--input", default="ao3_tag_metadata.csv",
                         help="Metadata CSV to read (default: ao3_tag_metadata.csv)")
    parser.add_argument("--top-fandoms", type=int, default=20,
                         help="Keep only the top N most frequent fandoms overall (default: 20)")
    parser.add_argument("--top-additional-tags", type=int, default=40,
                         help="Keep only the top N most frequent additional tags overall (default: 40)")
    parser.add_argument("--min-count", type=int, default=2,
                         help="Drop edges/cells below this co-occurrence count (default: 2)")
    parser.add_argument("--top-seed-tags", type=int, default=None,
                         help="Only include the N seed tags with the most works (default: all)")
    parser.add_argument("--network-out", default="ao3_tag_network.html",
                         help="Interactive network HTML output (default: ao3_tag_network.html)")
    parser.add_argument("--heatmap-out-dir", default="heatmaps",
                         help="Directory for heatmap PNGs (default: heatmaps)")
    parser.add_argument("--normalize", action="store_true",
                         help="Heatmap cells show %% of seed tag's works instead of raw counts")
    parser.add_argument("--network-only", action="store_true",
                         help="Only build the network, skip heatmaps")
    parser.add_argument("--heatmaps-only", action="store_true",
                         help="Only build heatmaps, skip the network")
    return parser


def build_field_data(df, top_fandoms, top_additional_tags, min_count):
    """Returns {field: filtered co-occurrence counts DataFrame} for FIELDS_TO_VISUALIZE."""
    top_n_by_field = {"fandom": top_fandoms, "additional_tags": top_additional_tags}
    field_tables = {}
    for field in FIELDS_TO_VISUALIZE:
        exploded = explode_field(df, field)
        n = top_n_by_field.get(field) if field in FIELDS_TOP_N_ELIGIBLE else None
        keep_values = top_n_values(exploded, field, n)
        counts = cooccurrence_counts(exploded, field, keep_values)
        counts = apply_min_count(counts, min_count)
        field_tables[field] = counts
    return field_tables


def main(argv=None):
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    df = load_metadata(args.input)
    seed_tags = rank_seed_tags(df, args.top_seed_tags)

    field_tables = build_field_data(df, args.top_fandoms, args.top_additional_tags, args.min_count)

    if not args.heatmaps_only:
        print("Building network graph")
        graph = build_bipartite_graph(field_tables, seed_tags)
        render_network(graph, args.network_out)

    if not args.network_only:
        print("Building heatmaps")
        os.makedirs(args.heatmap_out_dir, exist_ok=True)
        work_counts = df["tag"].value_counts() if args.normalize else None
        for field, counts in field_tables.items():
            matrix = cooccurrence_matrix(counts, field, seed_tags, normalize_by=work_counts)
            out_path = os.path.join(args.heatmap_out_dir, f"heatmap_{field}.png")
            render_heatmap(matrix, field, out_path, normalized=args.normalize)


if __name__ == "__main__":
    main()
