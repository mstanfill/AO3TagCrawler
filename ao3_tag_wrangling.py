#!/usr/bin/env python3
"""Literal-vs-wrangled tag usage per seed tag, from ao3_tag_metadata.csv.

Browsing an AO3 canonical tag page (the scraper's seed tags) returns works
tagged with the canonical tag OR any synonym/subtag wranglers merged into
it -- but the scraped metadata records each work's tags as the author
typed them. So the per-field heatmaps' same-name cells (seed tag "Angst" x
displayed tag "Angst" at, say, 47.5%) are not a bug: they measure how
often authors literally typed the canonical tag. This script makes that
measurement explicit:

  - literal usage: per seed tag, the share of its works that display the
    seed tag itself (case-insensitively -- AO3 tags are case-insensitively
    unique, so a case variant is the same tag, not a synonym) in any
    metadata field vs. works that matched via wrangling.
  - synonym breakdown (optional): given a relations CSV mapping each seed
    tag to its known synonyms/subtags (columns: seed_tag, relation,
    related_tag with relation in {synonym, subtag}; produced by hand
    today, or by a future scraper step), names the exact form each
    non-literal work used; works matching no known relation are counted
    as "unidentified".

No network access is required -- this only reads local CSVs.
"""
import argparse
import os
import sys

import pandas as pd

import ao3_tag_analysis as analysis
import ao3_tag_visualizer as viz


def _literal_pairs(df):
    """(tag, work_id) pairs where the work displays its seed tag literally
    (case-insensitively) in ANY metadata field -- a seed tag can be a
    fandom/relationship/character, not just an additional_tags value."""
    matches = []
    for field in analysis.ALL_METADATA_FIELDS:
        exploded = viz.explode_field(df, field)
        match = exploded[exploded[field].str.lower() == exploded["tag"].str.lower()]
        matches.append(match[["tag", "work_id"]])
    return pd.concat(matches, ignore_index=True).drop_duplicates()


def seed_tag_literal_usage(df):
    """Per seed tag: how many of its works display the seed tag literally
    vs. matched only via wrangling (synonyms/subtags). Returns a DataFrame
    [seed_tag, n_works, literal_works, literal_pct, wrangled_pct] sorted
    by n_works descending (tie-break: alphabetical). literal_pct +
    wrangled_pct always sums to 100 -- unlike the fandom-label outputs
    this is a strict two-way partition of each seed tag's works."""
    totals = (df.drop_duplicates(["tag", "work_id"])
                .groupby("tag")["work_id"].nunique())
    literal_counts = _literal_pairs(df).groupby("tag")["work_id"].nunique()

    result = pd.DataFrame({
        "seed_tag": totals.index,
        "n_works": totals.values,
        "literal_works": totals.index.map(literal_counts).fillna(0).astype(int),
    })
    result["literal_pct"] = (result["literal_works"] / result["n_works"] * 100).round(1)
    result["wrangled_pct"] = (100 - result["literal_pct"]).round(1)
    result = result.sort_values(["n_works", "seed_tag"], ascending=[False, True])
    return result.reset_index(drop=True)


def synonym_breakdown(df, relations_df):
    """Names the exact form each work used, given known wrangling
    relations. relations_df columns: seed_tag, relation, related_tag with
    relation in {synonym, subtag} (other relation values are ignored).
    Returns a DataFrame [seed_tag, matched_via, n_works, pct] where
    matched_via is "literal", the exact related-tag name, or
    "unidentified" (the work displays neither the seed tag nor any known
    relation -- e.g. a wrangling chain the relations CSV doesn't cover).
    A literal work counts once under "literal"; a non-literal work counts
    under EVERY known relation it displays (it can legitimately display
    several, so a seed tag's percentages can sum past 100 -- the same
    documented semantics as the fandom-label outputs); rows with zero
    works are omitted. pct denominator = the seed tag's total works."""
    pairs = df.drop_duplicates(["tag", "work_id"])
    totals = pairs.groupby("tag")["work_id"].nunique()

    literal = _literal_pairs(df)

    relations = relations_df[relations_df["relation"].isin(["synonym", "subtag"])].copy()
    relations["seed_lower"] = relations["seed_tag"].str.lower()
    relations["related_lower"] = relations["related_tag"].str.lower()

    matched_frames = []
    for field in analysis.ALL_METADATA_FIELDS:
        exploded = viz.explode_field(df, field)
        exploded = exploded.assign(seed_lower=exploded["tag"].str.lower(),
                                    value_lower=exploded[field].str.lower())
        joined = exploded.merge(relations, on="seed_lower")
        joined = joined[joined["value_lower"] == joined["related_lower"]]
        matched_frames.append(joined[["tag", "work_id", "related_tag"]])
    matched = pd.concat(matched_frames, ignore_index=True).drop_duplicates()
    # "otherwise": a literal work counts once, under literal only
    # (anti-join against the literal pairs).
    matched = matched.merge(literal.assign(_literal=True),
                             on=["tag", "work_id"], how="left")
    matched = matched[matched["_literal"].isna()].drop(columns="_literal")

    rows = []
    for tag, total in totals.items():
        literal_n = literal[literal["tag"] == tag]["work_id"].nunique()
        if literal_n:
            rows.append({"seed_tag": tag, "matched_via": "literal", "n_works": literal_n})
        tag_matched = matched[matched["tag"] == tag]
        for related_tag, group in tag_matched.groupby("related_tag"):
            rows.append({"seed_tag": tag, "matched_via": related_tag,
                          "n_works": group["work_id"].nunique()})
        identified_works = set(literal[literal["tag"] == tag]["work_id"]) | \
                            set(tag_matched["work_id"])
        unidentified_n = total - len(identified_works)
        if unidentified_n:
            rows.append({"seed_tag": tag, "matched_via": "unidentified",
                          "n_works": unidentified_n})

    result = pd.DataFrame(rows, columns=["seed_tag", "matched_via", "n_works"])
    result["pct"] = (result["n_works"] / result["seed_tag"].map(totals) * 100).round(1)
    # Seed tags in the same order as seed_tag_literal_usage (n_works
    # descending, tie-break alphabetical); within a seed tag, biggest
    # slice first (tie-break: alphabetical).
    order = totals.reset_index()
    order.columns = ["tag", "n_works"]
    order = order.sort_values(["n_works", "tag"], ascending=[False, True])
    tag_order = {tag: rank for rank, tag in enumerate(order["tag"])}
    result = result.assign(_order=result["seed_tag"].map(tag_order))
    result = result.sort_values(["_order", "n_works", "matched_via"],
                                 ascending=[True, False, True]).drop(columns="_order")
    return result.reset_index(drop=True)


def build_arg_parser():
    parser = argparse.ArgumentParser(
        description="Split each seed tag's works into literal-tag usage vs. "
                     "wrangled (synonym/subtag) matches, with an optional "
                     "per-synonym breakdown when a relations CSV is available.",
    )
    parser.add_argument("--input", default="ao3_tag_metadata.csv",
                         help="Metadata CSV to read (default: ao3_tag_metadata.csv)")
    parser.add_argument("--synonyms-csv", default="ao3_tag_synonyms.csv",
                         help="Wrangling relations CSV (seed_tag, relation, related_tag "
                              "with relation in {synonym, subtag}). If the file doesn't "
                              "exist, the per-synonym breakdown is skipped with a note "
                              "(default: ao3_tag_synonyms.csv)")
    parser.add_argument("--literal-out", default="ao3_seed_tag_literal_usage.csv",
                         help="Literal-vs-wrangled split CSV output "
                              "(default: ao3_seed_tag_literal_usage.csv)")
    parser.add_argument("--breakdown-out", default="ao3_seed_tag_synonym_breakdown.csv",
                         help="Per-synonym breakdown CSV output, only written when "
                              "--synonyms-csv exists "
                              "(default: ao3_seed_tag_synonym_breakdown.csv)")
    return parser


def main():
    args = build_arg_parser().parse_args()
    df = viz.load_metadata(args.input)

    literal_df = seed_tag_literal_usage(df)
    literal_df.to_csv(args.literal_out, index=False)
    print(f"wrote {args.literal_out} ({len(literal_df)} seed tags)")

    if os.path.exists(args.synonyms_csv):
        relations_df = pd.read_csv(args.synonyms_csv, dtype=str, keep_default_na=False)
        missing = {"seed_tag", "relation", "related_tag"} - set(relations_df.columns)
        if missing:
            print(f"error: {args.synonyms_csv} is missing columns {sorted(missing)}",
                  file=sys.stderr)
            sys.exit(1)
        breakdown_df = synonym_breakdown(df, relations_df)
        breakdown_df.to_csv(args.breakdown_out, index=False)
        print(f"wrote {args.breakdown_out} ({len(breakdown_df)} rows)")
    else:
        print(f"  note: {args.synonyms_csv} not found -- skipping the per-synonym "
              f"breakdown (the literal-usage split above never needs it)",
              file=sys.stderr)


if __name__ == "__main__":
    main()
