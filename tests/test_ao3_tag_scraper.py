#!/usr/bin/env python3
"""Exercises ao3_tag_scraper.py's offline paths: the tag-name <-> URL
helpers behind --tag/--tag-url and the tags-only CLI flow.

No network access needed -- the network-using steps (tag-cloud scrape,
work listings, metadata) are deliberately not exercised here. Run with:
    python tests/test_ao3_tag_scraper.py
"""
import csv
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ao3_tag_scraper as scraper

FAILURES = []


def check(name, condition, detail=""):
    if condition:
        print(f"PASS: {name}")
    else:
        print(f"FAIL: {name} {detail}")
        FAILURES.append(name)


def run_url_helper_checks():
    # tag_name_to_works_url: ordinary names percent-encode spaces (%20,
    # never +), and AO3's star-substitutions apply before quoting, with
    # the stars themselves left unescaped.
    check("plain spaced name encodes spaces as %20",
          scraper.tag_name_to_works_url("Creator Chose Not To Use Archive Warnings")
          == "https://archiveofourown.org/tags/"
             "Creator%20Chose%20Not%20To%20Use%20Archive%20Warnings/works")
    check("relationship name: / becomes *s*, with * left unescaped",
          scraper.tag_name_to_works_url("Bob/Carol")
          == "https://archiveofourown.org/tags/Bob*s*Carol/works")
    check("& . ? # all get their AO3 substitutions",
          scraper.tag_name_to_works_url("A&B.C?D#E")
          == "https://archiveofourown.org/tags/A*a*B*d*C*q*D*h*E/works")

    # tag_name_from_url: the user's exact motivating example, a
    # star-escaped URL, a tag landing page without /works, and a URL
    # carrying a query string.
    check("percent-encoded works URL recovers the display name",
          scraper.tag_name_from_url(
              "https://archiveofourown.org/tags/"
              "Choose%20Not%20To%20Use%20Archive%20Warnings/works")
          == "Choose Not To Use Archive Warnings")
    check("star-escaped URL recovers the relationship name",
          scraper.tag_name_from_url("https://archiveofourown.org/tags/Bob*s*Carol/works")
          == "Bob/Carol")
    check("tag landing-page URL (no /works) also works",
          scraper.tag_name_from_url("https://archiveofourown.org/tags/Angst") == "Angst")
    check("a query string doesn't leak into the name",
          scraper.tag_name_from_url(
              "https://archiveofourown.org/tags/Angst/works?page=2&view_adult=true")
          == "Angst")
    try:
        scraper.tag_name_from_url("https://archiveofourown.org/works/12345")
        check("a non-tag URL raises ValueError", False, "no exception raised")
    except ValueError:
        check("a non-tag URL raises ValueError", True)

    # Round-trip: a name exercising every substitution plus spaces.
    nasty = "A/B & C. D? E#F"
    check("name -> url -> name round-trips through every substitution",
          scraper.tag_name_from_url(scraper.tag_name_to_works_url(nasty)) == nasty)

    # resolve_start_tags: names + URLs mix, dedupe by name (first wins),
    # order preserved; URLs are normalized to /works form.
    tags = scraper.resolve_start_tags(
        ["Angst", "Bob/Carol"],
        ["https://archiveofourown.org/tags/Bob*s*Carol",  # dupe of the name form
         "https://archiveofourown.org/tags/Fluff/works"])
    check("resolve_start_tags mixes names and URLs, dedupes, preserves order",
          tags == [("Angst", "https://archiveofourown.org/tags/Angst/works"),
                    ("Bob/Carol", "https://archiveofourown.org/tags/Bob*s*Carol/works"),
                    ("Fluff", "https://archiveofourown.org/tags/Fluff/works")],
          f"got {tags}")
    check("resolve_start_tags handles None inputs",
          scraper.resolve_start_tags(None, None) == [])


def run_cli_checks(tmpdir, script_path):
    parser = scraper.build_arg_parser()
    default_args = parser.parse_args([])
    check("--tag defaults to None", default_args.tag is None)
    check("--tag-url defaults to None", default_args.tag_url is None)

    # Fully offline end-to-end: --tag + --tags-only writes the step-1 CSV
    # and stops before any network step.
    tags_out = os.path.join(tmpdir, "tags.csv")
    result = subprocess.run(
        [sys.executable, script_path, "--tag", "Bob/Carol",
         "--tag-url", "https://archiveofourown.org/tags/"
                       "Choose%20Not%20To%20Use%20Archive%20Warnings/works",
         "--tags-only", "--tags-out", tags_out],
        capture_output=True, text=True,
    )
    check("--tag/--tag-url with --tags-only exits 0 with no network use",
          result.returncode == 0, f"stderr: {result.stderr}")
    with open(tags_out, newline="", encoding="utf-8") as f:
        rows = {row["tag_name"]: row for row in csv.DictReader(f)}
    check("tags CSV has both provided tags",
          set(rows) == {"Bob/Carol", "Choose Not To Use Archive Warnings"},
          f"got {sorted(rows)}")
    check("tags CSV carries the star-escaped works URL for the name form",
          rows["Bob/Carol"]["tag_url"]
          == "https://archiveofourown.org/tags/Bob*s*Carol/works",
          f"got {rows['Bob/Carol']}")
    check("tags CSV keeps the URL form's percent-encoded works URL",
          rows["Choose Not To Use Archive Warnings"]["tag_url"]
          == "https://archiveofourown.org/tags/"
             "Choose%20Not%20To%20Use%20Archive%20Warnings/works",
          f"got {rows['Choose Not To Use Archive Warnings']}")
    check("tags CSV has the same columns as a scraped step 1",
          list(csv.DictReader(open(tags_out, encoding="utf-8")).fieldnames)
          == ["tag_name", "tag_href", "tag_url"])

    # The written CSV feeds back through --step2's loader unchanged.
    loaded = scraper.load_tags(tags_out)
    check("the written tags CSV round-trips through load_tags (--step2 compatible)",
          loaded == [("Bob/Carol",
                       "https://archiveofourown.org/tags/Bob*s*Carol/works"),
                      ("Choose Not To Use Archive Warnings",
                       "https://archiveofourown.org/tags/"
                       "Choose%20Not%20To%20Use%20Archive%20Warnings/works")],
          f"got {loaded}")

    # --tag combined with --step2/--step3 is a hard argparse error.
    result2 = subprocess.run(
        [sys.executable, script_path, "--tag", "Angst", "--step2", tags_out],
        capture_output=True, text=True,
    )
    check("--tag with --step2 exits 2 with a conflict error",
          result2.returncode == 2 and "can't be combined" in result2.stderr,
          f"rc={result2.returncode}, stderr: {result2.stderr}")
    result3 = subprocess.run(
        [sys.executable, script_path, "--tag-url",
         "https://archiveofourown.org/tags/Angst/works", "--step3", "whatever.csv"],
        capture_output=True, text=True,
    )
    check("--tag-url with --step3 exits 2 with a conflict error",
          result3.returncode == 2 and "can't be combined" in result3.stderr,
          f"rc={result3.returncode}, stderr: {result3.stderr}")


def main():
    tmpdir = tempfile.mkdtemp(prefix="ao3_scraper_test_")
    script_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "ao3_tag_scraper.py")

    run_url_helper_checks()
    run_cli_checks(tmpdir, script_path)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
        sys.exit(1)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
