#!/usr/bin/env python3
"""Labels rows of an existing tag CSV (e.g. ao3_tag_clusters.csv, from
ao3_tag_analysis.py) with the fandom(s) each tag is associated with,
computed from real co-occurrence in ao3_tag_metadata.csv.

For each tag_id, finds every work that contains it and looks at what
fandom(s) those works belong to, reporting the top N fandoms by
co-occurrence percentage (e.g. "Fandom A (62%), Fandom B (25%), Fandom C
(13%)"). This is descriptive co-occurrence computed directly from the
scrape, not a guess from the tag's name -- a fandom-field tag will
trivially show itself near 100% (works tagged with a fandom are, almost by
definition, in that fandom); a cross-cutting trope tag like "Angst" will
show a spread across many fandoms instead.

No network access is required -- this only reads local CSVs.
"""
import argparse
import sys

import pandas as pd

import ao3_tag_analysis as analysis
import ao3_tag_visualizer as viz


def compute_fandom_labels(df, tag_ids, top_n):
    """Returns a dict tag_id -> "Fandom A (62%), Fandom B (25%), ..."
    string, ranked by descending co-occurrence percentage (tie-break:
    alphabetically smallest fandom name), computed from real per-work
    co-occurrence in df. The percentage denominator is the tag's total
    document count (every work containing the tag), not just the subset
    with a known fandom, so a tag whose usage is partly on fandom-less
    works will show percentages that don't sum to 100 -- an honest
    reflection of the data rather than silently renormalizing it away.
    Crossover works with multiple fandoms can likewise push a tag's
    percentages to sum above 100, for the same reason (fandom is a
    multi-valued field, same as every other field this codebase pools).
    tag_ids absent from df's metadata entirely (a stale/mismatched
    --clusters-csv) get an empty string rather than raising."""
    tag_table = viz.build_document_tag_table(df, fields=analysis.ALL_METADATA_FIELDS)
    tag_table = tag_table[tag_table["tag_id"].isin(tag_ids)]
    tag_totals = tag_table.groupby("tag_id").size()

    fandom_table = viz.explode_field(df, "fandom")[["work_id", "fandom"]]

    merged = tag_table.merge(fandom_table, on="work_id")
    counts = merged.groupby(["tag_id", "fandom"]).size().reset_index(name="count")
    counts["pct"] = counts["count"] / counts["tag_id"].map(tag_totals) * 100

    # Rank/truncate with vectorized pandas ops (sort_values + groupby().head()),
    # not a Python-level loop over every tag_id's group -- at real --all-tags
    # scale (tens of thousands of tags) a per-group loop calling
    # assign/sort_values/head/itertuples paid pandas' per-call overhead once
    # per tag_id and took 32s at 26,427 tags; this version does the same
    # ranking in 0.9s, confirmed to produce identical output.
    counts = counts.sort_values(["tag_id", "count", "fandom"], ascending=[True, False, True])
    top = counts.groupby("tag_id", sort=False).head(top_n)
    top = top.assign(entry=top["fandom"] + " (" + top["pct"].round(0).astype(int).astype(str) + "%)")
    labels = top.groupby("tag_id", sort=False)["entry"].apply(", ".join).to_dict()

    for tag_id in tag_ids:
        labels.setdefault(tag_id, "")
    return labels


def build_arg_parser():
    parser = argparse.ArgumentParser(
        description="Label an existing tag CSV with the top-N fandoms each tag "
                     "co-occurs with, by percentage.",
    )
    parser.add_argument("--input", default="ao3_tag_metadata.csv",
                         help="Metadata CSV to compute co-occurrence from "
                              "(default: ao3_tag_metadata.csv)")
    parser.add_argument("--clusters-csv", default="ao3_tag_clusters.csv",
                         help="Tag CSV to label -- must have a tag_id column "
                              "(default: ao3_tag_clusters.csv)")
    parser.add_argument("--top-n", type=int, default=3,
                         help="Number of top co-occurring fandoms to report per tag "
                              "(default: 3)")
    parser.add_argument("--column-name", default="top_fandoms",
                         help="Name for the new fandom-label column (default: top_fandoms)")
    parser.add_argument("--out", default="ao3_tag_clusters_with_fandoms.csv",
                         help="Labeled CSV output -- written as a new file, --clusters-csv "
                              "is never overwritten (default: ao3_tag_clusters_with_fandoms.csv)")
    return parser


def main():
    args = build_arg_parser().parse_args()
    df = viz.load_metadata(args.input)
    clusters_df = pd.read_csv(args.clusters_csv, dtype=str, keep_default_na=False)
    if "tag_id" not in clusters_df.columns:
        print(f"error: {args.clusters_csv} has no tag_id column", file=sys.stderr)
        sys.exit(1)

    tag_ids = set(clusters_df["tag_id"])
    labels = compute_fandom_labels(df, tag_ids, args.top_n)

    missing = sum(1 for tag_id in tag_ids if labels.get(tag_id) == "")
    if missing:
        print(f"  warning: {missing} of {len(tag_ids)} tags in {args.clusters_csv} had no "
              f"fandom co-occurrence found in {args.input} (stale/mismatched input?)",
              file=sys.stderr)

    clusters_df[args.column_name] = clusters_df["tag_id"].map(labels)
    clusters_df.to_csv(args.out, index=False)
    print(f"wrote {args.out} ({len(clusters_df)} rows labeled)")


if __name__ == "__main__":
    main()
