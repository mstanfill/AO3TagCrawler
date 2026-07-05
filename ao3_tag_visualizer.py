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
import json
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


def _seed_tags_from_graph(graph):
    """Recovers the seed-tag label list, in rank order, from a graph built by
    build_bipartite_graph. Seed-tag nodes are added first there, and
    networkx/dict node storage preserves insertion order, so this avoids
    threading seed_tags through render_network() as a separate parameter."""
    return [data["label"] for _, data in graph.nodes(data=True)
            if data.get("group") == "seed_tag"]


_FILTER_PANEL_CSS = """
<style>
#ao3-filter-panel { font-family: sans-serif; font-size: 14px; padding: 10px 14px;
  border-bottom: 1px solid #ccc; background: #fafafa; }
#ao3-filter-panel .ao3-cat-label { margin-right: 14px; white-space: nowrap; }
#ao3-filter-panel .ao3-swatch { display: inline-block; width: 10px; height: 10px;
  border-radius: 50%; margin: 0 4px 0 4px; vertical-align: middle; }
#ao3-tag-picker { margin-top: 8px; position: relative; }
#ao3-tag-picker .ao3-hint { color: #777; font-weight: normal; font-size: 12px; }
#ao3-tag-chips { display: flex; flex-wrap: wrap; gap: 4px; margin: 6px 0; }
.ao3-tag-chip { background: #4C72B0; color: #fff; border-radius: 12px;
  padding: 2px 6px 2px 10px; font-size: 12px; display: inline-flex; align-items: center; }
.ao3-chip-remove { background: none; border: none; color: #fff; cursor: pointer;
  font-size: 14px; margin-left: 4px; line-height: 1; }
#ao3-tag-search { width: 260px; padding: 4px 6px; }
#ao3-tag-dropdown { position: absolute; z-index: 10; background: #fff; border: 1px solid #ccc;
  max-height: 220px; overflow-y: auto; width: 260px; }
.ao3-tag-option { padding: 4px 8px; cursor: pointer; }
.ao3-tag-option:hover { background: #eef; }
</style>
"""

_FILTER_PANEL_HTML = """
<div id="ao3-filter-panel">
  <div id="ao3-cat-checkboxes">__CHECKBOX_ITEMS__</div>
  <div id="ao3-tag-picker">
    <strong>Seed tags</strong> <span class="ao3-hint">(none selected = show all)</span>
    <div id="ao3-tag-chips"></div>
    <input type="text" id="ao3-tag-search" placeholder="Search seed tags…" autocomplete="off">
    <div id="ao3-tag-dropdown" hidden></div>
  </div>
</div>
""" + _FILTER_PANEL_CSS

_FILTER_SCRIPT_TEMPLATE = """
<script>
(function () {
  const ALL_SEED_TAGS = __SEED_TAGS_JSON__;
  let selectedTags = [];

  function buildNodeGroups() {
    // Built from our own nodes.get() call rather than reusing any of
    // pyvis's own internal globals -- nodes/edges/network are the stable
    // vis-network API surface, not an implementation detail.
    const map = {};
    for (const n of nodes.get()) map[n.id] = n.group;
    return map;
  }

  function buildSeedAdjacency() {
    // seed-tag-node-id -> Set of connected attribute-value-node-ids, built
    // once so applyFilters() never has to rescan every edge.
    const adj = {};
    for (const e of edges.get()) {
      const aSeed = String(e.from).startsWith("tag::");
      const bSeed = String(e.to).startsWith("tag::");
      if (aSeed && !bSeed) { (adj[e.from] = adj[e.from] || new Set()).add(e.to); }
      else if (bSeed && !aSeed) { (adj[e.to] = adj[e.to] || new Set()).add(e.from); }
    }
    return adj;
  }

  const NODE_GROUPS = buildNodeGroups();
  const SEED_ADJACENCY = buildSeedAdjacency();

  function applyFilters() {
    const checkedGroups = new Set(
      Array.from(document.querySelectorAll('.ao3-cat-checkbox:checked'))
           .map(function (cb) { return cb.dataset.group; })
    );
    const seedLabels = selectedTags.length ? selectedTags : ALL_SEED_TAGS;
    const visibleSeedIds = new Set(seedLabels.map(function (t) { return "tag::" + t; }));

    const reachableAttrIds = new Set();
    visibleSeedIds.forEach(function (seedId) {
      const neighbors = SEED_ADJACENCY[seedId];
      if (neighbors) neighbors.forEach(function (attrId) { reachableAttrIds.add(attrId); });
    });

    const updates = [];
    for (const nodeId in NODE_GROUPS) {
      const group = NODE_GROUPS[nodeId];
      let hidden;
      if (group === "seed_tag") {
        hidden = !visibleSeedIds.has(nodeId);
      } else {
        hidden = !(checkedGroups.has(group) && reachableAttrIds.has(nodeId));
      }
      updates.push({ id: nodeId, hidden: hidden });
    }
    nodes.update(updates); // vis-network auto-hides edges with a hidden endpoint
  }

  function matchingTags(query) {
    // Empty query -> the full remaining list (not []), so clicking/focusing
    // the search box opens the dropdown with every not-yet-selected tag,
    // not just typed-in matches. The scrollable dropdown (max-height in CSS)
    // handles a full ~200-tag list fine, so no arbitrary result cap either.
    const q = query.trim().toLowerCase();
    const available = ALL_SEED_TAGS.filter(function (t) { return selectedTags.indexOf(t) === -1; });
    if (!q) return available;
    return available.filter(function (t) { return t.toLowerCase().indexOf(q) !== -1; });
  }

  function renderDropdown(query) {
    const dropdown = document.getElementById('ao3-tag-dropdown');
    dropdown.innerHTML = "";
    const matches = matchingTags(query);
    matches.forEach(function (tag) {
      const opt = document.createElement('div');
      opt.className = 'ao3-tag-option';
      opt.textContent = tag;
      opt.dataset.tag = tag;
      dropdown.appendChild(opt);
    });
    dropdown.hidden = matches.length === 0;
  }

  function renderChips() {
    const chips = document.getElementById('ao3-tag-chips');
    chips.innerHTML = "";
    selectedTags.forEach(function (tag) {
      const chip = document.createElement('span');
      chip.className = 'ao3-tag-chip';
      const label = document.createElement('span');
      label.textContent = tag;
      const remove = document.createElement('button');
      remove.className = 'ao3-chip-remove';
      remove.textContent = '\\u00d7';
      remove.dataset.tag = tag;
      chip.appendChild(label);
      chip.appendChild(remove);
      chips.appendChild(chip);
    });
  }

  function addTag(tag) {
    if (selectedTags.indexOf(tag) === -1) selectedTags.push(tag);
    renderChips();
    applyFilters();
  }

  function removeTag(tag) {
    selectedTags = selectedTags.filter(function (t) { return t !== tag; });
    renderChips();
    applyFilters();
  }

  const searchInput = document.getElementById('ao3-tag-search');
  searchInput.addEventListener('input', function () { renderDropdown(searchInput.value); });
  // Open the full tag list on focus/click too, not just once the user has
  // typed something -- gives a visible "click to open" affordance like a
  // normal dropdown, instead of only reacting to keystrokes.
  searchInput.addEventListener('focus', function () { renderDropdown(searchInput.value); });
  searchInput.addEventListener('click', function () { renderDropdown(searchInput.value); });

  document.getElementById('ao3-tag-dropdown').addEventListener('click', function (e) {
    const opt = e.target.closest('.ao3-tag-option');
    if (!opt) return;
    addTag(opt.dataset.tag);
    searchInput.value = "";
    document.getElementById('ao3-tag-dropdown').hidden = true;
  });

  document.getElementById('ao3-tag-chips').addEventListener('click', function (e) {
    const btn = e.target.closest('.ao3-chip-remove');
    if (btn) removeTag(btn.dataset.tag);
  });

  document.addEventListener('click', function (e) {
    const dropdown = document.getElementById('ao3-tag-dropdown');
    if (!dropdown.contains(e.target) && e.target !== searchInput) dropdown.hidden = true;
  });

  document.querySelectorAll('.ao3-cat-checkbox').forEach(function (cb) {
    cb.addEventListener('change', applyFilters);
  });

  applyFilters();
})();
</script>
"""


def _filter_controls_html(graph):
    """Builds the injected filter-UI markup and script as a (panel_html,
    script_html) pair. Pure string building, no file I/O."""
    seed_tags = _seed_tags_from_graph(graph)
    # A literal "</script" substring in the JSON would prematurely close the
    # <script> tag at the HTML-tokenizer level regardless of correct
    # JS/JSON escaping -- guard against it independent of AO3 data.
    seed_tags_json = json.dumps(seed_tags).replace("</script", "<\\/script")

    checkbox_items = "\n".join(
        '<label class="ao3-cat-label">'
        f'<input type="checkbox" class="ao3-cat-checkbox" data-group="{field}" checked>'
        f'<span class="ao3-swatch" style="background-color:{FIELD_COLORS[field]};"></span>'
        f'{field}</label>'
        for field in FIELDS_TO_VISUALIZE
    )
    # FIELDS_TO_VISUALIZE / FIELD_COLORS are hardcoded module constants
    # (lowercase ascii identifiers, hex colors), never user data, so no
    # HTML-escaping is needed here. Seed-tag labels (arbitrary AO3 text) are
    # never interpolated into an HTML string anywhere in this function --
    # they only ever travel through the JSON-encoded ALL_SEED_TAGS array,
    # and the client renders them via textContent/dataset, never innerHTML.
    panel_html = _FILTER_PANEL_HTML.replace("__CHECKBOX_ITEMS__", checkbox_items)
    script_html = _FILTER_SCRIPT_TEMPLATE.replace("__SEED_TAGS_JSON__", seed_tags_json)
    return panel_html, script_html


def _inject_filter_controls(html_path, graph):
    with open(html_path, encoding="utf-8") as f:
        html = f.read()
    # pyvis's template always emits exactly one bare <body>/</body> pair;
    # assert instead of silently no-oping if a future pyvis version changes
    # this, so template drift fails loudly rather than shipping a graph with
    # no filter UI.
    assert html.count("<body>") == 1, "expected exactly one <body> tag"
    assert html.count("</body>") == 1, "expected exactly one </body> tag"
    panel_html, script_html = _filter_controls_html(graph)
    html = html.replace("<body>", "<body>" + panel_html, 1)
    html = html.replace("</body>", script_html + "</body>", 1)
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)


# layout/physics (including improvedLayout and avoidOverlap) are configured
# at network-construction time via Network.set_options() -- see
# _NETWORK_OPTIONS_JSON below -- not here. A later network.setOptions() call
# (this script only runs once the page loads, after the network already
# exists) is too late to prevent vis-network's improvedLayout initial
# positioning attempt, which can fail outright on graphs like this one
# ("This network could not be positioned by this version of the improved
# layout algorithm") and silently stall stabilization for a long time.
_STABILIZE_THEN_STOP_SCRIPT = """
<script>
(function () {
  // Physics is configured to run (with overlap avoidance) until the layout
  // settles; once it does, turn physics off entirely so the graph freezes
  // instead of drifting/jittering indefinitely.
  network.once("stabilizationIterationsDone", function () {
    network.setOptions({ physics: false });
  });
})();
</script>
"""


def _inject_stabilize_then_stop(html_path):
    with open(html_path, encoding="utf-8") as f:
        html = f.read()
    assert "</body>" in html, "expected a </body> tag"
    html = html.replace("</body>", _STABILIZE_THEN_STOP_SCRIPT + "</body>", 1)
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)


# Passed to Network.set_options() before write_html() -- must be strict
# JSON (pyvis's Options.set() does a bare json.loads on it, no JS-style
# comments/trailing commas/unquoted keys). avoidOverlap spreads nodes out
# enough to reduce label collisions once physics stabilizes (see
# _STABILIZE_THEN_STOP_SCRIPT); improvedLayout is disabled because vis-network's
# own clustering-based initial positioner can fail outright on graphs like
# this one and silently stall stabilization; iterations is capped low so
# stabilization can't run away indefinitely on a large/dense graph.
_NETWORK_OPTIONS_JSON = json.dumps({
    "layout": {"improvedLayout": False},
    "physics": {
        "solver": "barnesHut",
        "barnesHut": {"avoidOverlap": 0.5},
        "stabilization": {"enabled": True, "iterations": 200, "fit": True},
    },
})


def render_network(graph, out_path, notebook=False):
    # show_buttons() would pull in its own control-panel styling the same way
    # -- omitted so the output file stays self-contained.
    net = Network(height="800px", width="100%", notebook=notebook, cdn_resources="in_line")
    net.set_options(_NETWORK_OPTIONS_JSON)
    net.from_nx(graph)
    net.write_html(out_path, notebook=notebook)
    _strip_bootstrap_cdn(out_path)
    _inject_filter_controls(out_path, graph)
    _inject_stabilize_then_stop(out_path)
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
