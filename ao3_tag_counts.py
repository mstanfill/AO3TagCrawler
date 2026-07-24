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

A final "seed_tags" row describes something different: how many distinct
seed tags (the searched AO3 tags in the `tag` column) found each work. The
scraper emits one row per (seed tag, work), so a work matched by several of
your searched tags appears in several rows -- which is why the CSV has more
rows than distinct works, and its mean is exactly (row count) / (distinct
works).

No network access is required -- this only reads a local CSV.
"""
import argparse

import pandas as pd

import ao3_tag_analysis as analysis
import ao3_tag_visualizer as viz

STAT_COLUMNS = ["scope", "n_works", "total_tags", "mean", "std",
                "min", "p25", "median", "mode", "p75", "max"]


def _describe_counts(scope, counts_per_work, all_work_ids):
    """describe()-style stats row for a per-work count Series, reindexed to
    all_work_ids (fill 0) so every distinct work is in the denominator."""
    full = counts_per_work.reindex(all_work_ids, fill_value=0)
    desc = full.describe()  # count, mean, std, min, 25%, 50%, 75%, max
    std = desc["std"]
    # The most common per-work count. mode() returns every value tied for most
    # frequent, sorted ascending (and, over the zero-filled distribution, is
    # never empty), so .iloc[0] picks the smallest on a tie -- e.g. an all-
    # distinct distribution falls back to the minimum.
    mode = int(full.mode().iloc[0])
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
        "mode": mode,
        "p75": round(float(desc["75%"]), 1),
        "max": int(desc["max"]),
    }


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

    rows = [_describe_counts("all_fields", table.groupby("work_id").size(), all_work_ids)]
    for field in analysis.ALL_METADATA_FIELDS:
        field_counts = table[table["field"] == field].groupby("work_id").size()
        rows.append(_describe_counts(field, field_counts, all_work_ids))

    return pd.DataFrame(rows, columns=STAT_COLUMNS)


def seed_tags_per_work_stats(df):
    """Returns a one-row DataFrame (scope "seed_tags") describing how many
    DISTINCT seed tags -- the searched AO3 tags in the `tag` column -- found
    each work. This is scrape structure, NOT the work's own tags: the
    scraper emits one row per (seed tag, work), so a work matched by k of
    your searched tags appears in k rows. That is exactly why
    ao3_tag_metadata.csv has more rows than distinct works -- the mean here
    is (row count) / (distinct works). Distinct seed tags per work (not raw
    row count) so a re-scraped/duplicated (tag, work) row can't inflate it;
    every work has >= 1, so min is 1 and no zero-fill applies."""
    all_work_ids = pd.Index(df["work_id"].unique(), name="work_id")
    counts = df.groupby("work_id")["tag"].nunique()
    return pd.DataFrame([_describe_counts("seed_tags", counts, all_work_ids)],
                         columns=STAT_COLUMNS)


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
    stats = pd.concat([tags_per_story_stats(df), seed_tags_per_work_stats(df)],
                       ignore_index=True)
    stats.to_csv(args.out, index=False)
    print(f"wrote {args.out} ({stats.iloc[0]['n_works']} works)")
    print(stats.to_string(index=False))


if __name__ == "__main__":
    main()
