#!/usr/bin/env python3
"""Descriptive statistics on the number of tags per story, from
ao3_tag_metadata.csv.

Reports how many distinct tags each work carries, both pooled across all
seven tag-bearing metadata fields (rating, warnings, category, fandom,
relationship, character, additional_tags) and broken down per field, as a
standard describe()-style summary (count/mean/std/min/quartiles/max).

Three data-shape subtleties are handled by reusing ao3_tag_visualizer.py's
build_document_tag_table:
  - the scraper emits one row per (seed tag, work), so a work found via
    several seed tags appears several times -- deduped to one row per work;
  - a field cell holds comma-separated values, deduped within the cell;
  - a tag is namespaced field::value, so the same literal string in two
    different fields counts as two distinct tags.
Zeros are included: a story with no additional_tags counts as 0 additional
tags (not dropped), so the per-field means are over every story.

No network access is required -- this only reads a local CSV.
"""
import argparse

import pandas as pd

import ao3_tag_analysis as analysis
import ao3_tag_visualizer as viz

STAT_COLUMNS = ["scope", "n_works", "total_tags", "mean", "std",
                "min", "p25", "median", "p75", "max"]


def tags_per_story_stats(df):
    """Returns a DataFrame [scope, n_works, total_tags, mean, std, min,
    p25, median, p75, max], one row per scope: "all_fields" (every tag
    field pooled) followed by one row per field in ALL_METADATA_FIELDS.

    The denominator is every distinct work in df; a work with no tags in a
    given scope contributes 0 to that scope (zeros included), so the means
    describe every story, not just the ones that happen to use the field."""
    all_work_ids = pd.Index(df["work_id"].unique(), name="work_id")

    table = viz.build_document_tag_table(df, fields=analysis.ALL_METADATA_FIELDS)
    table = table.assign(field=table["tag_id"].str.split("::", n=1).str[0])

    def stats_for(scope, counts_per_work):
        full = counts_per_work.reindex(all_work_ids, fill_value=0)
        desc = full.describe()  # count, mean, std, min, 25%, 50%, 75%, max
        std = desc["std"]
        return {
            "scope": scope,
            "n_works": int(desc["count"]),
            "total_tags": int(full.sum()),
            "mean": round(float(desc["mean"]), 2),
            # std is NaN when there's a single work (pandas ddof=1); report 0.
            "std": round(float(std), 2) if pd.notna(std) else 0.0,
            "min": int(desc["min"]),
            "p25": round(float(desc["25%"]), 1),
            "median": round(float(desc["50%"]), 1),
            "p75": round(float(desc["75%"]), 1),
            "max": int(desc["max"]),
        }

    rows = [stats_for("all_fields", table.groupby("work_id").size())]
    for field in analysis.ALL_METADATA_FIELDS:
        field_counts = table[table["field"] == field].groupby("work_id").size()
        rows.append(stats_for(field, field_counts))

    return pd.DataFrame(rows, columns=STAT_COLUMNS)


def build_arg_parser():
    parser = argparse.ArgumentParser(
        description="Descriptive statistics on the number of tags per story, "
                     "pooled across all seven tag fields and broken down per field.",
    )
    parser.add_argument("--input", default="ao3_tag_metadata.csv",
                         help="Metadata CSV to read (default: ao3_tag_metadata.csv)")
    parser.add_argument("--out", default="ao3_tags_per_story_stats.csv",
                         help="Statistics CSV output (default: ao3_tags_per_story_stats.csv)")
    return parser


def main():
    args = build_arg_parser().parse_args()
    df = viz.load_metadata(args.input)
    stats = tags_per_story_stats(df)
    stats.to_csv(args.out, index=False)
    print(f"wrote {args.out} ({stats.iloc[0]['n_works']} works)")
    print(stats.to_string(index=False))


if __name__ == "__main__":
    main()
