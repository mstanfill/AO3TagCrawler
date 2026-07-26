#!/usr/bin/env python3
"""How many distinct fandoms each tag appears on ("fandom spread"), from
ao3_tag_metadata.csv.

For every value of a chosen tag field (default additional_tags), reports how
many DISTINCT fandoms co-occur with it and the top few fandoms by
co-occurrence percentage -- so a cross-cutting trope like "Angst" shows a
high n_fandoms spread across many fandoms, while a niche tag shows only a
handful. Rows are sorted most-cross-cutting first.

This reuses ao3_tag_fandom_labels.compute_fandom_summary, which already
computes n_fandoms/top_fandoms correctly, including the two data-shape traps:
  - the scraper emits one row per (seed tag, work), so a work found via
    several seed tags is deduped by work_id (not counted several times);
  - fandom is a comma-separated, multi-valued field (a crossover work counts
    each of its fandoms).
The percentage denominator is each tag's own total work count, so a tag used
partly on fandom-less works shows percentages that don't sum to 100 -- an
honest reflection of the data rather than silently renormalizing it away.

The default field is additional_tags; any tag-bearing field works, though
fandom-vs-itself is trivially ~1 and so not very interesting.

No network access is required -- this only reads a local CSV.
"""
import argparse

import pandas as pd

import ao3_tag_fandom_labels as labels
import ao3_tag_visualizer as viz

OUT_COLUMNS = ["field", "value", "n_works", "n_fandoms", "top_fandoms"]


def fandom_spread(df, field="additional_tags", top_n=3):
    """Returns a DataFrame [field, value, n_works, n_fandoms, top_fandoms],
    one row per distinct value of `field`, sorted by n_fandoms (then n_works,
    then value) so the most cross-cutting tags come first.

      - n_works: how many distinct works carry the tag.
      - n_fandoms: how many DISTINCT fandoms those works span.
      - top_fandoms: "Fandom A (62%), Fandom B (25%), ..." -- the top_n
        co-occurring fandoms by percentage (see
        ao3_tag_fandom_labels.compute_fandom_summary for the semantics).

    Returns an empty (correctly-columned) DataFrame when the field has no
    values in df."""
    tag_table = viz.build_document_tag_table(df, fields=[field])
    if tag_table.empty:
        return pd.DataFrame(columns=OUT_COLUMNS)

    n_works = tag_table.groupby("tag_id").size()
    tag_ids = set(tag_table["tag_id"].unique())
    summary = labels.compute_fandom_summary(df, tag_ids, top_n)

    # tag_id is namespaced f"{field}::{value}" (field names never contain "::").
    summary = summary.assign(
        field=summary["tag_id"].str.split("::", n=1).str[0],
        value=summary["tag_id"].str.split("::", n=1).str[1],
        n_works=summary["tag_id"].map(n_works).astype(int),
    )
    summary = summary.sort_values(
        ["n_fandoms", "n_works", "value"], ascending=[False, False, True])
    return summary[OUT_COLUMNS].reset_index(drop=True)


def build_arg_parser():
    parser = argparse.ArgumentParser(
        description="How many distinct fandoms each tag appears on, plus its top "
                     "co-occurring fandoms, for every value of a chosen tag field.",
    )
    parser.add_argument("--input", default="ao3_tag_metadata.csv",
                         help="Metadata CSV to read (default: ao3_tag_metadata.csv)")
    parser.add_argument("--field", default="additional_tags",
                         help="Tag field to analyze (default: additional_tags)")
    parser.add_argument("--top-n", type=int, default=3,
                         help="Number of top co-occurring fandoms to list per tag "
                              "(default: 3)")
    parser.add_argument("--out", default="ao3_tag_fandom_spread.csv",
                         help="Output CSV (default: ao3_tag_fandom_spread.csv)")
    return parser


def main():
    args = build_arg_parser().parse_args()
    df = viz.load_metadata(args.input)
    spread = fandom_spread(df, field=args.field, top_n=args.top_n)
    spread.to_csv(args.out, index=False)
    print(f"wrote {args.out} ({len(spread)} {args.field} values)")
    print(spread.head(20).to_string(index=False))


if __name__ == "__main__":
    main()
