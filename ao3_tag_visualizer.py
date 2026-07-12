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
import html
import json
import os
import re
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import networkx as nx
import numpy as np
import pandas as pd
import scipy.sparse as sp
import seaborn as sns
from pyvis.network import Network
from pyvis.node import Node
from pyvis.edge import Edge

DELIMITER = ", "

FIELDS_TO_VISUALIZE = ["rating", "warnings", "category", "fandom", "additional_tags"]
FIELDS_TOP_N_ELIGIBLE = {"fandom", "additional_tags"}

# Tag-pair co-occurrence (--tag-pairs): folksonomy-style tags only, not the
# more categorical rating/warnings/category fields.
TAG_PAIR_FIELDS = ["fandom", "relationship", "character", "additional_tags"]

FIELD_COLORS = {
    "seed_tag": "#4C72B0",
    "rating": "#DD8452",
    "warnings": "#55A868",
    "category": "#C44E52",
    "fandom": "#8172B2",
    "additional_tags": "#937860",
    "character": "#CCB974",
    "relationship": "#64B5CD",
}

MOST_LIKELY_EDGE_COLOR = "#2A9D8F"    # pair co-occurs more often than chance
LEAST_LIKELY_EDGE_COLOR = "#E63946"   # pair co-occurs less often than chance


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


def apply_min_proportion(counts, work_counts, min_proportion):
    """Alternative to apply_min_count: keeps a (tag, value) pair only if it
    appears in at least min_proportion (0.0-1.0) of that seed tag's own
    total works, rather than by a raw absolute count. work_counts is
    df["tag"].value_counts() -- every tag in `counts` is guaranteed present
    in it (both are derived from the same df), so no zero-division guard
    is needed; an empty `counts` flows through unchanged, same as
    apply_min_count."""
    denom = counts["tag"].map(work_counts)
    return counts[(counts["count"] / denom) >= min_proportion]


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


def render_heatmap(matrix, field, out_path, normalized=False, cmap="viridis",
                    center=None, xlabel=None, ylabel=None, cbar_label=None, fmt=None):
    if matrix.empty or matrix.shape[1] == 0:
        print(f"  skipping heatmap for {field}: no data after filtering", file=sys.stderr)
        return

    width = max(8, 0.35 * matrix.shape[1] + 3)
    height = max(6, 0.22 * matrix.shape[0] + 3)
    fig, ax = plt.subplots(figsize=(width, height))
    annot = matrix.shape[0] * matrix.shape[1] <= 500
    if fmt is None:
        fmt = ".1f" if normalized else "d"
    if cbar_label is None:
        cbar_label = "% of tag's works" if normalized else "co-occurrence count"
    # mask=matrix.isna() is a no-op for the existing pipeline's matrices
    # (never contain NaN); the tag-pair matrix uses NaN for "no data" (as
    # opposed to 0, a meaningful independence value), rendered as blank cells.
    sns.heatmap(matrix, annot=annot, fmt=fmt, cmap=cmap, center=center,
                mask=matrix.isna(), cbar_kws={"label": cbar_label}, ax=ax)
    ax.set_xlabel(xlabel if xlabel is not None else field)
    ax.set_ylabel(ylabel if ylabel is not None else "seed tag")
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    print(f"  wrote {out_path} ({matrix.shape[0]}x{matrix.shape[1]})")


def write_heatmap_csv(matrix, out_path):
    """The heatmap's exact matrix as CSV, for sorting/filtering in
    Excel/Sheets/pandas -- the PNG becomes unscannable once there are
    hundreds of seed-tag rows. NaN cells (the tag-pair matrix's "never
    co-occurs", deliberately distinct from 0) become empty CSV cells."""
    if matrix.empty or matrix.shape[1] == 0:
        print("  skipping heatmap CSV: no data after filtering", file=sys.stderr)
        return
    matrix.to_csv(out_path)
    print(f"  wrote {out_path} ({matrix.shape[0]}x{matrix.shape[1]})")


_HEATMAP_TABLE_CSS = """
<style>
body { font-family: sans-serif; font-size: 13px; margin: 0; }
#hm-controls { position: sticky; top: 0; z-index: 3; background: #fafafa;
  padding: 8px 12px; border-bottom: 1px solid #ccc; }
#hm-search { width: 260px; padding: 4px 6px; margin-right: 14px; }
#hm-legend-bar { display: inline-block; width: 160px; height: 12px;
  vertical-align: middle; margin: 0 6px; border: 1px solid #aaa; }
#hm-scroll { overflow: auto; max-height: calc(100vh - 46px); }
table { border-collapse: collapse; }
th, td { padding: 3px 7px; text-align: right; white-space: nowrap;
  border: 1px solid #e0e0e0; }
thead th { position: sticky; top: 0; z-index: 2; background: #f0f0f0;
  cursor: pointer; }
thead th:hover { background: #e0e8f0; }
tbody th { position: sticky; left: 0; z-index: 1; background: #f0f0f0;
  text-align: left; font-weight: normal; }
thead th:first-child { left: 0; z-index: 4; }
</style>
"""

_HEATMAP_SORT_SCRIPT = """
<script>
(function () {
  const table = document.getElementById("hm-table");
  const tbody = table.tBodies[0];
  const headers = table.tHead.rows[0].cells;
  let sortCol = null;
  let ascending = false;

  function sortRows(col) {
    // Column 0 is the row-label column: sort alphabetically (ascending
    // first). Data columns sort by the numeric data-v sort key
    // (descending first); blank/NaN cells always sink to the bottom.
    if (sortCol === col) { ascending = !ascending; }
    else { sortCol = col; ascending = (col === 0); }
    const rows = Array.from(tbody.rows);
    rows.sort(function (a, b) {
      if (col === 0) {
        const av = a.cells[0].textContent.toLowerCase();
        const bv = b.cells[0].textContent.toLowerCase();
        return ascending ? av.localeCompare(bv) : bv.localeCompare(av);
      }
      const araw = a.cells[col].dataset.v;
      const braw = b.cells[col].dataset.v;
      if (araw === "" && braw === "") return 0;
      if (araw === "") return 1;
      if (braw === "") return -1;
      return ascending ? (araw - braw) : (braw - araw);
    });
    rows.forEach(function (r) { tbody.appendChild(r); });
  }

  for (let i = 0; i < headers.length; i++) {
    headers[i].addEventListener("click", function () { sortRows(i); });
  }

  document.getElementById("hm-search").addEventListener("input", function () {
    const q = this.value.trim().toLowerCase();
    for (const row of tbody.rows) {
      row.hidden = q !== "" && row.cells[0].textContent.toLowerCase().indexOf(q) === -1;
    }
  });
})();
</script>
"""


def render_heatmap_html(matrix, field, out_path, normalized=False, cmap="viridis",
                         center=None, xlabel=None, ylabel=None, cbar_label=None, fmt=None):
    """The heatmap as a self-contained sortable/searchable HTML table --
    same signature, colormap, value format, and NaN masking as
    render_heatmap, but navigable where a huge PNG can only be scanned:
    sticky row/column headers, click any column header to sort by it
    (click again to flip; blanks always last; the corner header sorts
    row labels alphabetically), and a live search box filtering rows.
    Row labels and column values are arbitrary AO3 text, so everything
    is HTML-escaped and the sort keys travel in data-v attributes rather
    than being parsed back out of display text."""
    if matrix.empty or matrix.shape[1] == 0:
        print(f"  skipping heatmap HTML for {field}: no data after filtering",
              file=sys.stderr)
        return
    if fmt is None:
        fmt = ".1f" if normalized else "d"
    if cbar_label is None:
        cbar_label = "% of tag's works" if normalized else "co-occurrence count"

    values = matrix.to_numpy(dtype=float)
    finite = values[np.isfinite(values)]
    vmin = float(finite.min()) if finite.size else 0.0
    vmax = float(finite.max()) if finite.size else 1.0
    if center is not None:
        # Same symmetric range seaborn uses for center=: the colormap
        # midpoint lands exactly on `center`.
        spread = max(abs(vmin - center), abs(vmax - center)) or 1.0
        vmin, vmax = center - spread, center + spread
    if vmax == vmin:
        vmax = vmin + 1.0
    colormap = plt.get_cmap(cmap)

    def cell_style(value):
        r, g, b, _ = colormap((value - vmin) / (vmax - vmin))
        # Perceived luminance decides black vs. white cell text.
        luminance = 0.299 * r + 0.587 * g + 0.114 * b
        text = "#000" if luminance > 0.5 else "#fff"
        return (f"background-color:#{int(r * 255):02x}{int(g * 255):02x}"
                f"{int(b * 255):02x};color:{text}")

    corner = html.escape(ylabel if ylabel is not None else "seed tag")
    header_cells = "".join(
        f"<th>{html.escape(str(column))}</th>" for column in matrix.columns)
    body_rows = []
    for row_label, row in matrix.iterrows():
        cells = [f"<th>{html.escape(str(row_label))}</th>"]
        for value in row.to_numpy(dtype=float):
            if not np.isfinite(value):
                cells.append('<td data-v=""></td>')
            else:
                # float(value), not value directly: numpy scalars repr as
                # "np.float64(57.6)", which the JS numeric comparator turns
                # into NaN, silently breaking column sorting.
                cells.append(f'<td data-v="{float(value)!r}" style="{cell_style(value)}">'
                             f"{format(value, fmt) if fmt != 'd' else int(value)}</td>")
        body_rows.append("<tr>" + "".join(cells) + "</tr>")

    gradient_stops = ", ".join(
        "#" + "".join(f"{int(channel * 255):02x}" for channel in colormap(i / 10)[:3])
        for i in range(11))
    document = (
        "<!DOCTYPE html>\n<html><head><meta charset=\"utf-8\">"
        f"<title>heatmap: {html.escape(field)}</title>{_HEATMAP_TABLE_CSS}</head><body>\n"
        '<div id="hm-controls">'
        '<input type="text" id="hm-search" placeholder="Search rows…" autocomplete="off">'
        f"<span>{html.escape(cbar_label)}:</span>"
        f"<span>{format(vmin, fmt) if fmt != 'd' else int(vmin)}</span>"
        f'<span id="hm-legend-bar" style="background:linear-gradient(to right, {gradient_stops});"></span>'
        f"<span>{format(vmax, fmt) if fmt != 'd' else int(vmax)}</span>"
        "<span style=\"margin-left:14px;color:#777;\">click a column header to sort</span>"
        "</div>\n"
        '<div id="hm-scroll"><table id="hm-table">'
        f"<thead><tr><th>{corner}</th>{header_cells}</tr></thead>"
        f"<tbody>{''.join(body_rows)}</tbody></table></div>\n"
        f"{_HEATMAP_SORT_SCRIPT}</body></html>\n"
    )
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(document)
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
/* Bounded + scrollable: harmless for a handful of checkboxes (no scrollbar
   when content fits), essential for the cluster network, where real
   --all-tags data can produce thousands of clusters -- an unbounded
   checkbox list fills pages and pushes the graph canvas below the fold. */
#ao3-cat-checkboxes { max-height: 108px; overflow-y: auto; }
#ao3-filter-panel .ao3-cat-label { margin-right: 14px; white-space: nowrap;
  display: inline-block; }
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


def _all_tags_from_graph(graph):
    """Recovers [{"id", "label", "field"}] for every node, in insertion
    order, for the tag-pair picker. Unlike the bipartite picker (which
    stores plain labels, since seed tags can't collide with each other),
    this stores the full namespaced node id as the selection key -- label
    alone is exactly what CAN collide across fields (e.g. a fandom and a
    character sharing a literal name)."""
    return [{"id": node_id, "label": data["label"], "field": data["group"]}
            for node_id, data in graph.nodes(data=True)]


_TAG_PAIR_FILTER_PANEL_HTML = """
<div id="ao3-filter-panel">
  <div id="ao3-cat-checkboxes">__CHECKBOX_ITEMS__</div>
  <div id="ao3-tag-picker">
    <strong>Tags</strong> <span class="ao3-hint">(none selected = show all;
    selecting narrows to those tags + their direct connections)</span>
    <div id="ao3-tag-chips"></div>
    <input type="text" id="ao3-tag-search" placeholder="Search tags…" autocomplete="off">
    <div id="ao3-tag-dropdown" hidden></div>
  </div>
</div>
""" + _FILTER_PANEL_CSS

_TAG_PAIR_FILTER_SCRIPT_TEMPLATE = """
<script>
(function () {
  const ALL_TAGS = __ALL_TAGS_JSON__;
  let selectedTagIds = [];

  function buildNodeGroups() {
    const map = {};
    for (const n of nodes.get()) map[n.id] = n.group;
    return map;
  }

  function buildFullAdjacency() {
    // Every node here is a "tag" -- unlike the bipartite graph, there's no
    // privileged always-visible node class, so adjacency is symmetric and
    // built for every node, not just one side of a bipartite split.
    const adj = {};
    for (const e of edges.get()) {
      (adj[e.from] = adj[e.from] || new Set()).add(e.to);
      (adj[e.to] = adj[e.to] || new Set()).add(e.from);
    }
    return adj;
  }

  const NODE_GROUPS = buildNodeGroups();
  const FULL_ADJACENCY = buildFullAdjacency();

  function applyFilters() {
    const checkedGroups = new Set(
      Array.from(document.querySelectorAll('.ao3-cat-checkbox:checked'))
           .map(function (cb) { return cb.dataset.group; })
    );
    let reachable = null;
    if (selectedTagIds.length) {
      reachable = new Set();
      selectedTagIds.forEach(function (id) {
        reachable.add(id);
        const neighbors = FULL_ADJACENCY[id];
        if (neighbors) neighbors.forEach(function (n) { reachable.add(n); });
      });
    }
    const updates = [];
    for (const nodeId in NODE_GROUPS) {
      let hidden = !checkedGroups.has(NODE_GROUPS[nodeId]);
      if (!hidden && reachable) hidden = !reachable.has(nodeId);
      updates.push({ id: nodeId, hidden: hidden });
    }
    nodes.update(updates); // vis-network auto-hides edges with a hidden endpoint
  }

  function tagById(id) {
    return ALL_TAGS.find(function (t) { return t.id === id; });
  }

  function matchingTags(query) {
    const q = query.trim().toLowerCase();
    const available = ALL_TAGS.filter(function (t) { return selectedTagIds.indexOf(t.id) === -1; });
    if (!q) return available;
    return available.filter(function (t) {
      return t.label.toLowerCase().indexOf(q) !== -1 || t.field.toLowerCase().indexOf(q) !== -1;
    });
  }

  function renderDropdown(query) {
    const dropdown = document.getElementById('ao3-tag-dropdown');
    dropdown.innerHTML = "";
    const matches = matchingTags(query);
    matches.forEach(function (t) {
      const opt = document.createElement('div');
      opt.className = 'ao3-tag-option';
      opt.textContent = t.label + " (" + t.field + ")";
      opt.dataset.id = t.id;
      dropdown.appendChild(opt);
    });
    dropdown.hidden = matches.length === 0;
  }

  function renderChips() {
    const chips = document.getElementById('ao3-tag-chips');
    chips.innerHTML = "";
    selectedTagIds.forEach(function (id) {
      const t = tagById(id);
      const chip = document.createElement('span');
      chip.className = 'ao3-tag-chip';
      const label = document.createElement('span');
      label.textContent = t ? (t.label + " (" + t.field + ")") : id;
      const remove = document.createElement('button');
      remove.className = 'ao3-chip-remove';
      remove.textContent = '\\u00d7';
      remove.dataset.id = id;
      chip.appendChild(label);
      chip.appendChild(remove);
      chips.appendChild(chip);
    });
  }

  function addTag(id) {
    if (selectedTagIds.indexOf(id) === -1) selectedTagIds.push(id);
    renderChips();
    applyFilters();
  }

  function removeTag(id) {
    selectedTagIds = selectedTagIds.filter(function (x) { return x !== id; });
    renderChips();
    applyFilters();
  }

  const searchInput = document.getElementById('ao3-tag-search');
  searchInput.addEventListener('input', function () { renderDropdown(searchInput.value); });
  searchInput.addEventListener('focus', function () { renderDropdown(searchInput.value); });
  searchInput.addEventListener('click', function () { renderDropdown(searchInput.value); });

  document.getElementById('ao3-tag-dropdown').addEventListener('click', function (e) {
    const opt = e.target.closest('.ao3-tag-option');
    if (!opt) return;
    addTag(opt.dataset.id);
    searchInput.value = "";
    document.getElementById('ao3-tag-dropdown').hidden = true;
  });

  document.getElementById('ao3-tag-chips').addEventListener('click', function (e) {
    const btn = e.target.closest('.ao3-chip-remove');
    if (btn) removeTag(btn.dataset.id);
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


def _tag_pair_filter_controls_html(graph):
    """Same shape as _filter_controls_html, for the one-mode tag-pair graph:
    checkboxes iterate TAG_PAIR_FIELDS (every node's group is checkbox-
    controlled -- there's no privileged always-visible class like
    seed_tag), and the tag picker's underlying data is
    [{"id","label","field"}, ...] rather than a flat label list, since
    label alone can collide across fields."""
    all_tags = _all_tags_from_graph(graph)
    all_tags_json = json.dumps(all_tags).replace("</script", "<\\/script")

    checkbox_items = "\n".join(
        '<label class="ao3-cat-label">'
        f'<input type="checkbox" class="ao3-cat-checkbox" data-group="{field}" checked>'
        f'<span class="ao3-swatch" style="background-color:{FIELD_COLORS[field]};"></span>'
        f'{field}</label>'
        for field in TAG_PAIR_FIELDS
    )
    panel_html = _TAG_PAIR_FILTER_PANEL_HTML.replace("__CHECKBOX_ITEMS__", checkbox_items)
    script_html = _TAG_PAIR_FILTER_SCRIPT_TEMPLATE.replace("__ALL_TAGS_JSON__", all_tags_json)
    return panel_html, script_html


def _inject_tag_pair_filter_controls(html_path, graph):
    with open(html_path, encoding="utf-8") as f:
        html = f.read()
    assert html.count("<body>") == 1, "expected exactly one <body> tag"
    assert html.count("</body>") == 1, "expected exactly one </body> tag"
    panel_html, script_html = _tag_pair_filter_controls_html(graph)
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

# Used when the caller has already baked fixed x/y positions into every
# node (see ao3_tag_analysis.py's compute_cluster_layout) -- physics off
# means vis-network places nodes at exactly those coordinates and never
# runs a client-side force simulation over them, which is the part that
# makes a real --all-tags-scale graph (tens of thousands of nodes) hang or
# crash the tab outright, independent of file size.
_STATIC_NETWORK_OPTIONS_JSON = json.dumps({
    "layout": {"improvedLayout": False},
    "physics": {"enabled": False},
})


def _fast_populate_network(net, graph, default_node_size=10, default_edge_weight=1):
    """Equivalent to pyvis's Network.from_nx(graph), confirmed to produce
    byte-identical net.nodes/net.edges/net.node_ids/net.node_map for the
    same input graph -- but without pyvis's own from_nx()/add_node()/
    add_edge(), which check membership via `x in self.node_ids` and
    `x in self.edges` (plain Python lists, not sets), an O(V) or O(E) scan
    on every single node/edge insertion. At real --all-tags scale that's
    catastrophic: confirmed directly, from_nx() alone took 174s at just
    10,000 nodes/50,000 edges, and was still running after 66 minutes at
    40,000 nodes/200,000 edges. A plain nx.Graph can't have duplicate
    edges between the same pair, and every node here is added exactly
    once by the caller, so none of pyvis's duplicate-checking is actually
    needed -- this builds the identical Node/Edge option dicts pyvis's own
    classes produce, appending directly instead. Fixed: 0.53s for the same
    40,000-node/200,000-edge graph."""
    for node_id, data in graph.nodes(data=True):
        opts = dict(data)
        opts.setdefault("size", default_node_size)
        opts["size"] = int(opts["size"])
        label = opts.pop("label", None) or node_id
        shape = opts.pop("shape", "dot")
        color = opts.pop("color", "#97c2fc")
        if "group" in opts:
            n = Node(node_id, shape, label=label, font_color=net.font_color, **opts)
        else:
            n = Node(node_id, shape, label=label, color=color, font_color=net.font_color, **opts)
        net.nodes.append(n.options)
        net.node_ids.append(node_id)
        net.node_map[node_id] = n.options

    for source, target, data in graph.edges(data=True):
        opts = dict(data)
        if "weight" in opts and "width" not in opts:
            opts["width"] = opts.pop("weight")
        elif "weight" not in opts and "width" not in opts:
            opts["width"] = default_edge_weight
        e = Edge(source, target, net.directed, **opts)
        net.edges.append(e.options)


def render_network(graph, out_path, notebook=False, inject_filters=_inject_filter_controls,
                    physics=True):
    # show_buttons() would pull in its own control-panel styling the same way
    # -- omitted so the output file stays self-contained.
    net = Network(height="800px", width="100%", notebook=notebook, cdn_resources="in_line")
    net.set_options(_NETWORK_OPTIONS_JSON if physics else _STATIC_NETWORK_OPTIONS_JSON)
    _fast_populate_network(net, graph)
    net.write_html(out_path, notebook=notebook)
    _strip_bootstrap_cdn(out_path)
    inject_filters(out_path, graph)
    if physics:
        # only meaningful when physics is actually running -- with physics
        # disabled from the start there's no stabilization to wait for.
        _inject_stabilize_then_stop(out_path)
    print(f"  wrote {out_path} ({graph.number_of_nodes()} nodes, {graph.number_of_edges()} edges)")
    return net


# ---------------------------------------------------------------------------
# Tag-pair co-occurrence statistics (--tag-pairs)
#
# A different question from the rest of this module: not "which attribute
# values does a seed tag associate with", but "which pairs of tags, pooled
# across fandom/relationship/character/additional_tags, statistically tend
# to co-occur (or avoid each other) more than chance would predict". Raw
# co-occurrence counts conflate "both tags are individually common" with
# "these two tags are actually associated" -- lift and PMI correct for that.
# ---------------------------------------------------------------------------

def build_document_tag_table(df, fields=TAG_PAIR_FIELDS):
    """Long (work_id, tag_id) table, one row per (distinct work, namespaced
    tag). A work matching multiple seed-tag searches gets a separate row
    per seed tag in df, all sharing the same work_id and identical
    fandom/relationship/character/additional_tags content (same AO3 work
    page) -- so df is deduplicated to one row per work_id first; this
    analysis doesn't care which seed tag(s) found a work, only its own
    content. tag_id is namespaced f"{field}::{value}" (same scheme
    build_bipartite_graph uses) so e.g. a fandom and a character sharing a
    literal name are never conflated."""
    deduped = df.drop_duplicates(subset="work_id", keep="first")
    tables = []
    for field in fields:
        exploded = explode_field(deduped, field)
        exploded = exploded.rename(columns={field: "value"})
        exploded["tag_id"] = field + "::" + exploded["value"]
        tables.append(exploded[["work_id", "tag_id"]])
    return pd.concat(tables, ignore_index=True)


def top_k_tags_by_document_frequency(tag_table, k):
    """Analogous to top_n_values, but over the unified tag pool at the
    work/document level (tag_table has at most one row per (work_id,
    tag_id), so counting rows == counting documents). Returns None (no
    filtering) if k is None. Ties broken alphabetically, same convention
    as top_n_values."""
    if k is None:
        return None
    counts = tag_table["tag_id"].value_counts().reset_index()
    counts.columns = ["tag_id", "count"]
    counts = counts.sort_values(["count", "tag_id"], ascending=[False, True])
    return set(counts["tag_id"].head(k))


def build_tag_incidence_matrix(tag_table, keep_tags):
    """Documents x tags boolean incidence matrix (index=work_id,
    columns=sorted keep_tags), returned as a sparse-dtype pandas DataFrame
    (int8) -- built directly from (work_id, tag_id) pairs via scipy.sparse
    rather than pd.crosstab, which densifies internally (pivot_table builds
    a dense (n_docs x n_tags) float64 array before the ">0"/astype("int8")
    steps ever get a chance to shrink it -- 4+ GiB at 81,037 tags, a second
    real MemoryError after --all-tags, one step earlier in the pipeline
    than the joint-matrix MemoryError tag_pair_statistics already guards
    against). .shape, .columns, and label-based column indexing/.sum() all
    keep working exactly as with a plain dense DataFrame -- only how this
    is built, and how tag_pair_statistics reads it, are sparse now.

    NOTE: its row count is only documents containing >=1 kept tag -- it is
    NOT the total document count and must never be used as n_docs (a work
    with none of the top-K tags has no row here, but still belongs in
    P(A)'s denominator). Column sums (per-tag totals) ARE always correct
    regardless of this row restriction."""
    filtered = tag_table[tag_table["tag_id"].isin(keep_tags)]
    columns = sorted(keep_tags)
    work_ids = sorted(filtered["work_id"].unique())
    col_index = {tag_id: i for i, tag_id in enumerate(columns)}
    row_index = {work_id: i for i, work_id in enumerate(work_ids)}

    row_idx = filtered["work_id"].map(row_index).to_numpy()
    col_idx = filtered["tag_id"].map(col_index).to_numpy()
    matrix = sp.coo_matrix(
        (np.ones(len(filtered), dtype=np.int8), (row_idx, col_idx)),
        shape=(len(work_ids), len(columns)), dtype=np.int8,
    ).tocsr()
    # Collapse any duplicate (work_id, tag_id) rows to a plain 0/1 presence
    # flag -- matches pd.crosstab(...) > 0's semantics regardless of raw
    # counts (coo_matrix sums duplicate entries on conversion to csr).
    matrix.data[:] = 1
    return pd.DataFrame.sparse.from_spmatrix(matrix, index=work_ids, columns=columns)


def tag_pair_statistics(incidence, n_docs):
    """Fully vectorized pairwise lift/PMI -- no nested Python loop over
    pairs, and no dense tags x tags matrix either: joint = incidence.T @
    incidence is computed in SciPy sparse space, since real tag pools can
    be tens of thousands of tags wide (--all-tags), where a dense tags x
    tags matrix would need tens of gigabytes even though the overwhelming
    majority of tag pairs never co-occur (e.g. 81,037 tags -> 6.6 billion
    cells -> 48.9 GiB just for that one array, which is exactly the
    MemoryError this sparse rewrite fixes). incidence itself is read via
    its .sparse.to_coo() accessor rather than to_numpy(), so it's never
    densified either -- build_tag_incidence_matrix already returns it as a
    sparse-dtype DataFrame for the same reason. joint's diagonal is each
    tag's own total document
    count (marginal). n_docs must be the TRUE total document count
    (df["work_id"].nunique() on the original, undeduplicated df --
    nunique() already ignores duplicate rows), never incidence.shape[0]
    (see build_tag_incidence_matrix) -- using the latter would silently
    undercount the corpus and skew every lift/PMI value.

    lift(A,B) = joint_count * n_docs / (count_a * count_b); pmi =
    log2(lift). Pairs with joint_count == 0 are dropped before the log2
    call (never assigned -inf) -- they carry no real co-occurrence signal.

    Returns a long DataFrame [tag_a, tag_b, joint_count, count_a, count_b,
    lift, pmi], one row per unordered pair with tag_a < tag_b (enforced by
    this function itself, below -- not merely inherited from the caller's
    column order), joint_count > 0.
    """
    tags = list(incidence.columns)
    values = incidence.sparse.to_coo().tocsr().astype(np.int64)
    joint = values.T @ values
    marginal = np.asarray(joint.diagonal()).ravel()

    # scipy.sparse.triu only stores the upper triangle's nonzero entries --
    # equivalent to np.triu_indices(k=1) on the dense matrix, but without
    # ever materializing the full tags x tags array to get there.
    upper = sp.triu(joint, k=1).tocoo()
    i_idx, j_idx, joint_counts = upper.row, upper.col, upper.data
    nonzero = joint_counts > 0
    i_idx, j_idx, joint_counts = i_idx[nonzero], j_idx[nonzero], joint_counts[nonzero]

    count_a = marginal[i_idx]
    count_b = marginal[j_idx]
    lift = (joint_counts * n_docs) / (count_a * count_b)
    pmi = np.log2(lift)

    # Explicit canonicalization: tag_a is always the alphabetically-smaller
    # of the pair, regardless of the incidence matrix's own column order --
    # np.triu_indices above only guarantees i < j by column *position*, not
    # by alphabetical value. This used to be true only as an incidental side
    # effect of build_tag_incidence_matrix always pre-sorting its columns;
    # enforcing it here means the guarantee holds for any caller, not just
    # today's one. lift/pmi are symmetric in count_a/count_b already, so
    # only the names and their paired counts need to swap together.
    tags_arr = np.array(tags)
    a_names = tags_arr[i_idx]
    b_names = tags_arr[j_idx]
    swap = a_names > b_names
    tag_a = np.where(swap, b_names, a_names)
    tag_b = np.where(swap, a_names, b_names)
    count_a_final = np.where(swap, count_b, count_a)
    count_b_final = np.where(swap, count_a, count_b)

    return pd.DataFrame({
        "tag_a": tag_a,
        "tag_b": tag_b,
        "joint_count": joint_counts,
        "count_a": count_a_final,
        "count_b": count_b_final,
        "lift": lift,
        "pmi": pmi,
    })


def apply_min_pair_count(pair_stats, min_pair_count):
    """Analogous to apply_min_count. Must run before apply_pmi_thresholds --
    this is what keeps a pair like (count_a=1, count_b=1, joint=1), whose
    lift is an enormous but statistically meaningless n_docs, out of the
    ranked/thresholded output."""
    return pair_stats[pair_stats["joint_count"] >= min_pair_count]


def apply_pmi_thresholds(pair_stats, min_pmi, max_pmi):
    """Keeps a pair iff pmi >= min_pmi (co-occurs more than chance, "most
    likely") OR pmi <= max_pmi (co-occurs less than chance, "least
    likely"), dropping the "boring middle" near independence."""
    return pair_stats[(pair_stats["pmi"] >= min_pmi) | (pair_stats["pmi"] <= max_pmi)]


def tag_pair_matrix(pair_stats, tags, value_col="pmi"):
    """Symmetric tag x tag DataFrame (index=columns=sorted(tags)). Cells
    for pairs absent from pair_stats (filtered out upstream, or originally
    joint_count==0) are NaN, not 0 -- 0 is itself a meaningful PMI value
    (independence) and must not be confused with "no data". The diagonal
    is always NaN. tags is the full top-K keep_tags set (not just tags
    that survived pair filtering), so a tag with zero surviving pairs
    still appears as an all-NaN row/column, mirroring the existing "Found
    Family stays as an all-zero row" precedent for the seed-tag heatmaps."""
    ordered = sorted(tags)
    matrix = pd.DataFrame(np.nan, index=ordered, columns=ordered)
    for _, row in pair_stats.iterrows():
        if row["tag_a"] in matrix.index and row["tag_b"] in matrix.index:
            matrix.loc[row["tag_a"], row["tag_b"]] = row[value_col]
            matrix.loc[row["tag_b"], row["tag_a"]] = row[value_col]
    return matrix


def build_tag_pair_graph(pair_stats):
    """One-mode (non-bipartite) graph: node id = the tag_id itself (e.g.
    "fandom::Alpha"), label = the part after "::", group/color = the part
    before "::" (field recovered from the id -- no separate lookup table
    needed, since namespacing already encodes it)."""
    graph = nx.Graph()
    for _, row in pair_stats.iterrows():
        for tag_id in (row["tag_a"], row["tag_b"]):
            if tag_id not in graph:
                field, _, value = tag_id.partition("::")
                graph.add_node(tag_id, label=value, group=field,
                                color=FIELD_COLORS[field], title=tag_id)
        relation = "more likely than chance" if row["pmi"] > 0 else "less likely than chance"
        edge_color = MOST_LIKELY_EDGE_COLOR if row["pmi"] > 0 else LEAST_LIKELY_EDGE_COLOR
        graph.add_edge(
            row["tag_a"], row["tag_b"], weight=int(row["joint_count"]),
            pmi=float(row["pmi"]), lift=float(row["lift"]),
            joint_count=int(row["joint_count"]), color=edge_color,
            title=(f"{row['tag_a']} × {row['tag_b']}: {relation} "
                   f"(lift={row['lift']:.2f}, pmi={row['pmi']:.2f}, "
                   f"joint count={int(row['joint_count'])})"),
        )
    return graph


def build_tag_pair_data(df, top_tags, min_pair_count, min_pmi, max_pmi):
    """Orchestrator, analogous to build_field_data. Returns (pair_stats,
    keep_tags) -- keep_tags is the full top-K tag universe (needed by
    tag_pair_matrix separately from pair_stats, which only has surviving
    pairs)."""
    tag_table = build_document_tag_table(df)
    keep_tags = top_k_tags_by_document_frequency(tag_table, top_tags)
    if keep_tags is None:
        keep_tags = set(tag_table["tag_id"].unique())
    incidence = build_tag_incidence_matrix(tag_table, keep_tags)
    n_docs = df["work_id"].nunique()
    pair_stats = tag_pair_statistics(incidence, n_docs)
    pair_stats = apply_min_pair_count(pair_stats, min_pair_count)
    pair_stats = apply_pmi_thresholds(pair_stats, min_pmi, max_pmi)
    return pair_stats, keep_tags


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _proportion_type(value):
    f = float(value)
    if not (0.0 <= f <= 1.0):
        raise argparse.ArgumentTypeError(f"must be between 0.0 and 1.0 (got {value!r})")
    return f


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
    count_group = parser.add_mutually_exclusive_group()
    # default=None (not 2) here is deliberate: argparse's mutually exclusive
    # group only flags a conflict when a value differs from that argument's
    # own default, not based on whether it was actually typed on the command
    # line -- with default=2, explicitly passing "--min-count 2
    # --min-proportion 0.1" would silently NOT raise a conflict (2 == its own
    # default), even though both were given. The effective default of 2 is
    # applied after parsing instead (see main()).
    count_group.add_argument("--min-count", type=int, default=None,
                              help="Drop edges/cells below this co-occurrence count "
                                   "(default: 2). Mutually exclusive with --min-proportion")
    count_group.add_argument("--min-proportion", type=_proportion_type, default=None,
                              help="Alternative to --min-count: drop a (seed tag, value) "
                                   "edge/cell if its co-occurrence count is below this "
                                   "fraction (0.0-1.0) of that seed tag's total works, e.g. "
                                   "0.1 keeps only values appearing in at least 10%% of the "
                                   "tag's works (default: disabled, --min-count applies "
                                   "instead). Mutually exclusive with --min-count")
    parser.add_argument("--top-seed-tags", type=int, default=None,
                         help="Only include the N seed tags with the most works (default: all)")
    parser.add_argument("--network-out", default="ao3_tag_network.html",
                         help="Interactive network HTML output (default: ao3_tag_network.html)")
    parser.add_argument("--heatmap-out-dir", default="heatmaps",
                         help="Directory for heatmap PNGs (default: heatmaps)")
    parser.add_argument("--network-only", action="store_true",
                         help="Only build the network, skip heatmaps")
    parser.add_argument("--heatmaps-only", action="store_true",
                         help="Only build heatmaps, skip the network")
    parser.add_argument("--tag-pairs", action="store_true",
                         help="Also compute/render tag-pair co-occurrence statistics "
                              "(lift/PMI) across fandom/relationship/character/"
                              "additional_tags -- which pairs of tags co-occur "
                              "statistically more or less than chance (default: off)")
    parser.add_argument("--top-tags", type=int, default=40,
                         help="For --tag-pairs: top N tags overall, by document "
                              "frequency, before computing pairwise stats (default: 40)")
    parser.add_argument("--min-pair-count", type=int, default=2,
                         help="For --tag-pairs: drop pairs co-occurring fewer than this "
                              "many times -- lift/PMI is unreliable at tiny sample sizes "
                              "(default: 2)")
    parser.add_argument("--min-pmi", type=float, default=1.0,
                         help="For --tag-pairs: \"most likely\" threshold -- keep pairs "
                              "with PMI (log2 of lift) at or above this (default: 1.0, "
                              "i.e. co-occurring at least twice as often as chance)")
    parser.add_argument("--max-pmi", type=float, default=-1.0,
                         help="For --tag-pairs: \"least likely\" threshold -- keep pairs "
                              "with PMI at or below this (default: -1.0, i.e. co-occurring "
                              "at most half as often as chance)")
    parser.add_argument("--tag-pair-network-out", default="ao3_tag_pair_network.html",
                         help="For --tag-pairs: network HTML output "
                              "(default: ao3_tag_pair_network.html)")
    parser.add_argument("--tag-pair-heatmap-out", default=None,
                         help="For --tag-pairs: heatmap PNG output "
                              "(default: <--heatmap-out-dir>/heatmap_tag_pairs.png)")
    return parser


def build_field_data(df, top_fandoms, top_additional_tags, work_counts=None,
                      min_count=None, min_proportion=None):
    """Returns {field: filtered co-occurrence counts DataFrame} for FIELDS_TO_VISUALIZE.
    Exactly one of min_count/min_proportion is expected to be non-None -- the
    caller (main()'s mutually exclusive CLI flags, or the notebook's own
    assertion) is responsible for that. work_counts (df["tag"].value_counts())
    is required when min_proportion is used."""
    top_n_by_field = {"fandom": top_fandoms, "additional_tags": top_additional_tags}
    field_tables = {}
    for field in FIELDS_TO_VISUALIZE:
        exploded = explode_field(df, field)
        n = top_n_by_field.get(field) if field in FIELDS_TOP_N_ELIGIBLE else None
        keep_values = top_n_values(exploded, field, n)
        counts = cooccurrence_counts(exploded, field, keep_values)
        if min_proportion is not None:
            counts = apply_min_proportion(counts, work_counts, min_proportion)
        else:
            counts = apply_min_count(counts, min_count)
        field_tables[field] = counts
    return field_tables


def main(argv=None):
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if args.min_count is None and args.min_proportion is None:
        args.min_count = 2  # effective default, applied here rather than in
                            # argparse itself -- see build_arg_parser()

    df = load_metadata(args.input)
    seed_tags = rank_seed_tags(df, args.top_seed_tags)

    # Needed unconditionally: heatmap cells are always colored/labeled by
    # percentage of each seed tag's works (not raw count), and --min-proportion
    # filtering needs the same per-tag totals when active.
    work_counts = df["tag"].value_counts()

    field_tables = build_field_data(df, args.top_fandoms, args.top_additional_tags,
                                     work_counts=work_counts,
                                     min_count=args.min_count,
                                     min_proportion=args.min_proportion)

    if not args.heatmaps_only:
        print("Building network graph")
        graph = build_bipartite_graph(field_tables, seed_tags)
        render_network(graph, args.network_out)

    if not args.network_only:
        print("Building heatmaps")
        os.makedirs(args.heatmap_out_dir, exist_ok=True)
        for field, counts in field_tables.items():
            matrix = cooccurrence_matrix(counts, field, seed_tags, normalize_by=work_counts)
            out_path = os.path.join(args.heatmap_out_dir, f"heatmap_{field}.png")
            render_heatmap(matrix, field, out_path, normalized=True)
            base = os.path.splitext(out_path)[0]
            write_heatmap_csv(matrix, base + ".csv")
            render_heatmap_html(matrix, field, base + ".html", normalized=True)

    if args.tag_pairs:
        if args.min_pmi <= args.max_pmi:
            print(f"  warning: --min-pmi ({args.min_pmi}) <= --max-pmi ({args.max_pmi}) -- "
                  "these bands overlap and will include nearly all pairs", file=sys.stderr)

        pair_stats, keep_tags = build_tag_pair_data(
            df, args.top_tags, args.min_pair_count, args.min_pmi, args.max_pmi)

        if not args.heatmaps_only:
            print("Building tag-pair network graph")
            tag_pair_graph = build_tag_pair_graph(pair_stats)
            render_network(tag_pair_graph, args.tag_pair_network_out,
                            inject_filters=_inject_tag_pair_filter_controls)

        if not args.network_only:
            print("Building tag-pair heatmap")
            matrix = tag_pair_matrix(pair_stats, keep_tags, value_col="pmi")
            out_path = args.tag_pair_heatmap_out or os.path.join(
                args.heatmap_out_dir, "heatmap_tag_pairs.png")
            os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
            render_heatmap(matrix, "tag pair", out_path, cmap="coolwarm", center=0,
                           xlabel="tag", ylabel="tag", cbar_label="PMI (log2 lift)", fmt=".2f")
            base = os.path.splitext(out_path)[0]
            write_heatmap_csv(matrix, base + ".csv")
            render_heatmap_html(matrix, "tag pair", base + ".html", cmap="coolwarm",
                                 center=0, xlabel="tag", ylabel="tag",
                                 cbar_label="PMI (log2 lift)", fmt=".2f")


if __name__ == "__main__":
    main()
