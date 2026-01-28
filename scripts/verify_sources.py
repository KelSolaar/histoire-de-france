# /// script
# dependencies = [
#   "click",
# ]
# requires-python = ">=3.10"
# ///

"""
Source Verification Script

Verifies that timeline entry excerpts match actual chapter text and fixes
line ranges. Handles exact matches, ellipsis-abbreviated excerpts, and
fuzzy matches. Flags true hallucinations for manual review.

Usage:
    uv run scripts/verify_sources.py
    uv run scripts/verify_sources.py --dry-run
    uv run scripts/verify_sources.py --input data/timeline_entries.json --chapters data/chapters/
"""

import copy
import difflib
import json
import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import click

# Module-level logger
LOGGER = logging.getLogger(__name__)

# Fuzzy match threshold
FUZZY_THRESHOLD = 0.75


# ==============================================================================
# CONFIGURATION
# ==============================================================================


@dataclass
class VerifyConfig:
    """Configuration settings for source verification."""

    input_file: Path = Path("data/timeline_entries.json")
    chapters_dir: Path = Path("data/chapters")
    fuzzy_threshold: float = FUZZY_THRESHOLD


# ==============================================================================
# UTILITY FUNCTIONS
# ==============================================================================


def setup_logging(log_level: int = logging.INFO) -> logging.Logger:
    """Setup logging configuration."""
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)33s - %(levelname)8s - %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
        ],
    )

    return LOGGER


def load_chapter(chapters_dir: Path, chapter: int) -> list[str]:
    """Load a chapter file and return its lines."""
    path = chapters_dir / f"chapter_{chapter:02d}.txt"
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8").splitlines()


def join_lines(lines: list[str], start: int, end: int) -> str:
    """Join lines from start to end (1-indexed) into a single string."""
    selected = lines[start - 1 : end]
    return " ".join(line.strip() for line in selected if line.strip())


def normalize_whitespace(text: str) -> str:
    """Collapse all whitespace to single spaces."""
    return " ".join(text.split())


# ==============================================================================
# EXCERPT LOCATION
# ==============================================================================


def find_excerpt_in_text(excerpt: str, full_text: str) -> int:
    """Find the position of an excerpt in the full text. Returns -1 if not found."""
    norm_excerpt = normalize_whitespace(excerpt)
    norm_text = normalize_whitespace(full_text)
    return norm_text.find(norm_excerpt)


def find_line_range_for_text(
    needle: str, lines: list[str]
) -> tuple[int, int] | None:
    """
    Find the tightest line range containing the needle text.

    Returns (line_start, line_end) as 1-indexed, or None if not found.
    """
    norm_needle = normalize_whitespace(needle)

    # Build a mapping: for each line, accumulate the running text
    # so we can find which lines contain the needle
    running = ""
    line_positions: list[tuple[int, int]] = []  # (start_pos, end_pos) in running
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            line_positions.append((len(running), len(running)))
            continue
        start = len(running)
        if running:
            running += " "
            start = len(running)
        running += stripped
        line_positions.append((start, len(running)))

    pos = running.find(norm_needle)
    if pos == -1:
        return None

    needle_end = pos + len(norm_needle)

    # Find first and last line that overlaps with the needle
    first_line = None
    last_line = None
    for i, (ls, le) in enumerate(line_positions):
        if le > pos and ls < needle_end:
            if first_line is None:
                first_line = i + 1  # 1-indexed
            last_line = i + 1

    if first_line is None:
        return None

    return (first_line, last_line)


# ==============================================================================
# VERIFICATION STRATEGIES
# ==============================================================================


@dataclass
class VerifyResult:
    """Result of verifying a single entry."""

    status: str  # "ok", "range_fixed", "excerpt_fixed", "flagged"
    new_line_start: int | None = None
    new_line_end: int | None = None
    new_excerpt: str | None = None
    detail: str = ""


def verify_exact_in_range(
    excerpt: str, lines: list[str], line_start: int, line_end: int
) -> VerifyResult | None:
    """Check if excerpt exists verbatim in the referenced line range."""
    referenced_text = join_lines(lines, line_start, line_end)
    if normalize_whitespace(excerpt) in normalize_whitespace(referenced_text):
        return VerifyResult(status="ok")
    return None


def verify_exact_in_chapter(
    excerpt: str, lines: list[str]
) -> VerifyResult | None:
    """Check if excerpt exists verbatim anywhere in the chapter."""
    full_text = " ".join(line.strip() for line in lines if line.strip())
    if find_excerpt_in_text(excerpt, full_text) == -1:
        return None

    # Found — compute correct line range
    result = find_line_range_for_text(excerpt, lines)
    if result is None:
        return None

    return VerifyResult(
        status="range_fixed",
        new_line_start=result[0],
        new_line_end=result[1],
        detail="excerpt found in chapter, line range corrected",
    )


def fuzzy_find_fragment(
    fragment: str, full_words: list[str], threshold: float = 0.80
) -> str | None:
    """Find the best fuzzy match for a fragment in the full text words."""
    norm_frag = normalize_whitespace(fragment)
    frag_words = norm_frag.split()
    n = len(frag_words)

    if n == 0 or len(full_words) < n:
        return None

    # Use anchor words to narrow search
    anchors = [w for w in frag_words if len(w) > 4][:3]
    if not anchors:
        anchors = frag_words[:2]

    candidates: set[int] = set()
    for anchor in anchors:
        a_lower = anchor.lower()
        for i, w in enumerate(full_words):
            if w.lower() == a_lower:
                candidates.add(max(0, i - n))

    if not candidates:
        return None

    best_ratio = 0.0
    best_match = ""
    for start in candidates:
        for delta in range(3):
            ws = n + delta
            if start + ws > len(full_words):
                continue
            candidate = " ".join(full_words[start : start + ws])
            ratio = difflib.SequenceMatcher(None, norm_frag, candidate).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_match = candidate

    if best_ratio >= threshold:
        return best_match
    return None


def verify_ellipsis_fragments(
    excerpt: str, lines: list[str]
) -> VerifyResult | None:
    """
    Handle excerpts with '...' or ellipsis by verifying each fragment.

    Tries exact match first, then fuzzy match for each fragment.
    If all fragments are found, builds a range spanning all of them
    and reconstructs the excerpt with actual verbatim text.
    """
    if "..." not in excerpt and "\u2026" not in excerpt:
        return None

    # Split on ellipsis variants
    fragments = re.split(r"\.{2,}|\u2026", excerpt)
    fragments = [f.strip() for f in fragments if f.strip()]

    if not fragments:
        return None

    full_text = " ".join(line.strip() for line in lines if line.strip())
    norm_full = normalize_whitespace(full_text)
    full_words = norm_full.split()

    # Verify each fragment (exact or fuzzy) and collect actual text
    actual_fragments: list[str] = []
    any_replaced = False
    for fragment in fragments:
        norm_frag = normalize_whitespace(fragment)
        if len(norm_frag) < 8:
            actual_fragments.append(fragment)
            continue
        if norm_frag in norm_full:
            actual_fragments.append(fragment)
        else:
            match = fuzzy_find_fragment(fragment, full_words)
            if match is None:
                return None
            actual_fragments.append(match)
            any_replaced = True

    # Find line range spanning all fragments
    min_start = len(lines) + 1
    max_end = 0
    for frag in actual_fragments:
        norm_frag = normalize_whitespace(frag)
        if len(norm_frag) < 8:
            continue
        result = find_line_range_for_text(frag, lines)
        if result:
            min_start = min(min_start, result[0])
            max_end = max(max_end, result[1])

    if max_end == 0:
        return None

    # Reconstruct excerpt with actual text
    new_excerpt = "... ".join(actual_fragments) if any_replaced else None

    return VerifyResult(
        status="excerpt_fixed" if any_replaced else "range_fixed",
        new_line_start=min_start,
        new_line_end=max_end,
        new_excerpt=new_excerpt,
        detail="ellipsis fragments verified (fuzzy), excerpt corrected"
        if any_replaced
        else "ellipsis fragments verified, line range corrected",
    )


def verify_fuzzy_match(
    excerpt: str, lines: list[str], threshold: float
) -> VerifyResult | None:
    """
    Find a fuzzy match for the excerpt in the chapter text.

    Uses anchor words to narrow the search region, then compares
    only a small window around each anchor hit.
    """
    full_text = " ".join(line.strip() for line in lines if line.strip())
    norm_full = normalize_whitespace(full_text)
    norm_excerpt = normalize_whitespace(excerpt)

    words_excerpt = norm_excerpt.split()
    n = len(words_excerpt)

    if n < 3:
        return None

    words_full = norm_full.split()
    if len(words_full) < n:
        return None

    # Use distinctive words (>4 chars) from the excerpt as anchors
    anchors = [w for w in words_excerpt if len(w) > 4][:5]
    if not anchors:
        anchors = words_excerpt[:3]

    # Find candidate positions where anchors appear
    candidate_positions: set[int] = set()
    for anchor in anchors:
        anchor_lower = anchor.lower()
        for i, w in enumerate(words_full):
            if w.lower() == anchor_lower:
                # Check a window around this position
                start = max(0, i - n)
                candidate_positions.add(start)

    if not candidate_positions:
        return None

    best_ratio = 0.0
    best_match = ""

    for start_pos in candidate_positions:
        for delta in range(0, 3):
            window_size = n + delta
            end_pos = start_pos + window_size
            if end_pos > len(words_full):
                continue
            candidate = " ".join(words_full[start_pos:end_pos])
            ratio = difflib.SequenceMatcher(
                None, norm_excerpt, candidate
            ).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_match = candidate

    if best_ratio < threshold:
        return None

    # Find line range for the matched text
    result = find_line_range_for_text(best_match, lines)
    if result is None:
        return None

    return VerifyResult(
        status="excerpt_fixed",
        new_line_start=result[0],
        new_line_end=result[1],
        new_excerpt=best_match,
        detail=f"fuzzy match ({best_ratio:.2f}), excerpt replaced with actual text",
    )


# ==============================================================================
# VERBATIM EXTRACTION
# ==============================================================================


def extract_verbatim_excerpt(
    excerpt: str, lines: list[str]
) -> VerifyResult | None:
    """
    Last resort: find the longest verbatim substring from the chapter
    that overlaps with the excerpt's key phrases.

    Looks for runs of 3+ consecutive words from the excerpt that exist
    verbatim in the chapter, picks the longest such run.
    """
    full_text = " ".join(line.strip() for line in lines if line.strip())
    norm_full = normalize_whitespace(full_text)

    # Strip ellipsis from excerpt, get clean words
    clean = re.sub(r"\.{2,}|…", " ", excerpt)
    norm_excerpt = normalize_whitespace(clean)
    words = norm_excerpt.split()

    if len(words) < 4:
        return None

    # Find the longest consecutive run of words that exists in the chapter
    best_start = 0
    best_len = 0

    for i in range(len(words)):
        # Binary search for max length starting at i
        lo, hi = 3, len(words) - i
        longest = 0
        while lo <= hi:
            mid = (lo + hi) // 2
            candidate = " ".join(words[i : i + mid])
            if candidate in norm_full:
                longest = mid
                lo = mid + 1
            else:
                hi = mid - 1
        if longest > best_len:
            best_len = longest
            best_start = i

    if best_len < 4:
        return None

    verbatim = " ".join(words[best_start : best_start + best_len])

    # Find line range
    result = find_line_range_for_text(verbatim, lines)
    if result is None:
        return None

    return VerifyResult(
        status="excerpt_fixed",
        new_line_start=result[0],
        new_line_end=result[1],
        new_excerpt=verbatim,
        detail=f"verbatim excerpt extracted ({best_len} words)",
    )


# ==============================================================================
# RANGE TIGHTENING
# ==============================================================================


def tighten_range(
    excerpt: str, lines: list[str], line_start: int, line_end: int
) -> tuple[int, int]:
    """
    Tighten an oversized line range to just the lines containing the excerpt.

    Returns the tightened (line_start, line_end).
    """
    result = find_line_range_for_text(excerpt, lines)
    if result is None:
        return (line_start, line_end)

    tight_start, tight_end = result

    # Only tighten if the current range is larger
    if tight_end - tight_start < line_end - line_start:
        return (tight_start, tight_end)

    return (line_start, line_end)


# ==============================================================================
# MAIN VERIFICATION
# ==============================================================================


@dataclass
class VerifySummary:
    """Aggregated verification results."""

    total: int = 0
    ok: int = 0
    range_fixed: int = 0
    excerpt_fixed: int = 0
    tightened: int = 0
    flagged: int = 0
    missing_chapter: int = 0
    flagged_entries: list[dict] = field(default_factory=list)


def verify_entry(
    entry: dict, chapter_lines: list[str], config: VerifyConfig
) -> VerifyResult:
    """Verify a single timeline entry's source against chapter text."""
    source = entry["source"]
    excerpt = source["excerpt"]
    line_start = source["line_start"]
    line_end = source["line_end"]

    # Strategy 1: Exact match in referenced range
    result = verify_exact_in_range(excerpt, chapter_lines, line_start, line_end)
    if result:
        return result

    # Strategy 2: Exact match elsewhere in chapter
    result = verify_exact_in_chapter(excerpt, chapter_lines)
    if result:
        return result

    # Strategy 3: Ellipsis fragments
    result = verify_ellipsis_fragments(excerpt, chapter_lines)
    if result:
        return result

    # Strategy 4: Fuzzy match
    result = verify_fuzzy_match(excerpt, chapter_lines, config.fuzzy_threshold)
    if result:
        return result

    # Strategy 5: Extract verbatim text from chapter using anchor words
    result = extract_verbatim_excerpt(excerpt, chapter_lines)
    if result:
        return result

    # No match found
    return VerifyResult(
        status="flagged",
        detail="excerpt not found in chapter text",
    )


def verify_all(
    entries: list[dict], config: VerifyConfig
) -> tuple[list[dict], VerifySummary]:
    """
    Verify all entries and return fixed entries + summary.

    Returns a new list of entries with fixes applied.
    """
    summary = VerifySummary()
    chapter_cache: dict[int, list[str]] = {}
    fixed_entries = []

    for entry in entries:
        summary.total += 1
        source = entry["source"]
        chapter = source["chapter"]

        # Load chapter (cached)
        if chapter not in chapter_cache:
            chapter_lines = load_chapter(config.chapters_dir, chapter)
            chapter_cache[chapter] = chapter_lines
        else:
            chapter_lines = chapter_cache[chapter]

        if not chapter_lines:
            summary.missing_chapter += 1
            LOGGER.warning(
                "%s: chapter %d not found", entry.get("id", "?"), chapter
            )
            fixed_entries.append(entry)
            continue

        # Verify
        result = verify_entry(entry, chapter_lines, config)

        # Apply fixes
        new_entry = copy.deepcopy(entry)
        new_source = new_entry["source"]

        if result.status == "ok":
            summary.ok += 1
        elif result.status == "range_fixed":
            summary.range_fixed += 1
            new_source["line_start"] = result.new_line_start
            new_source["line_end"] = result.new_line_end
            LOGGER.info(
                "%s: %s (was %d-%d, now %d-%d)",
                entry.get("id", "?"),
                result.detail,
                source["line_start"],
                source["line_end"],
                result.new_line_start,
                result.new_line_end,
            )
        elif result.status == "excerpt_fixed":
            summary.excerpt_fixed += 1
            new_source["line_start"] = result.new_line_start
            new_source["line_end"] = result.new_line_end
            new_source["excerpt"] = result.new_excerpt
            LOGGER.info(
                "%s: %s", entry.get("id", "?"), result.detail
            )
        elif result.status == "flagged":
            summary.flagged += 1
            summary.flagged_entries.append(
                {
                    "id": entry.get("id", "?"),
                    "chapter": chapter,
                    "excerpt": source["excerpt"][:120],
                }
            )
            LOGGER.warning(
                "%s: %s — %.120s",
                entry.get("id", "?"),
                result.detail,
                source["excerpt"],
            )

        # Tighten range for all non-flagged entries
        if result.status != "flagged":
            ls = new_source["line_start"]
            le = new_source["line_end"]
            tight_start, tight_end = tighten_range(
                new_source["excerpt"], chapter_lines, ls, le
            )
            if (tight_start, tight_end) != (ls, le):
                summary.tightened += 1
                LOGGER.debug(
                    "%s: range tightened %d-%d -> %d-%d",
                    entry.get("id", "?"),
                    ls,
                    le,
                    tight_start,
                    tight_end,
                )
                new_source["line_start"] = tight_start
                new_source["line_end"] = tight_end

        fixed_entries.append(new_entry)

    return fixed_entries, summary


# ==============================================================================
# MAIN
# ==============================================================================


@click.command()
@click.option(
    "--input",
    "-i",
    "input_file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=Path("data/timeline_entries.json"),
    help="Input timeline entries JSON file.",
)
@click.option(
    "--chapters",
    "-c",
    "chapters_dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=Path("data/chapters"),
    help="Directory containing chapter text files.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Report only, do not write changes.",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    default=False,
    help="Enable debug logging.",
)
def main(
    input_file: Path, chapters_dir: Path, dry_run: bool, verbose: bool
):
    """
    Verify timeline entry excerpts against chapter text and fix line ranges.

    Checks that each excerpt exists in the referenced chapter, corrects
    line ranges, handles ellipsis-abbreviated quotes, and flags entries
    that cannot be matched.

    Examples:

        uv run scripts/verify_sources.py

        uv run scripts/verify_sources.py --dry-run

        uv run scripts/verify_sources.py -i data/timeline_entries.json -c data/chapters/
    """
    setup_logging(logging.DEBUG if verbose else logging.INFO)

    LOGGER.info("Loading entries from %s", input_file)
    entries = json.loads(input_file.read_text(encoding="utf-8"))
    LOGGER.info("Loaded %d entries", len(entries))

    config = VerifyConfig(
        input_file=input_file,
        chapters_dir=chapters_dir,
    )

    fixed_entries, summary = verify_all(entries, config)

    # Summary
    click.echo("\n" + "=" * 60)
    click.echo("VERIFICATION SUMMARY")
    click.echo("=" * 60)
    click.echo(f"Total entries:     {summary.total}")
    click.echo(f"OK (exact match):  {summary.ok}")
    click.echo(f"Range fixed:       {summary.range_fixed}")
    click.echo(f"Excerpt fixed:     {summary.excerpt_fixed}")
    click.echo(f"Range tightened:   {summary.tightened}")
    click.echo(f"Flagged:           {summary.flagged}")
    if summary.missing_chapter:
        click.echo(f"Missing chapters:  {summary.missing_chapter}")
    click.echo("=" * 60)

    if summary.flagged_entries:
        click.echo("\nFlagged entries:")
        for fe in summary.flagged_entries:
            click.echo(f"  {fe['id']} ch{fe['chapter']}: {fe['excerpt']}...")

    if dry_run:
        click.echo("\nDry run — no changes written.")
    else:
        output = json.dumps(fixed_entries, ensure_ascii=False, indent=2)
        input_file.write_text(output + "\n", encoding="utf-8")
        click.echo(f"\nWrote {len(fixed_entries)} entries to {input_file}")


if __name__ == "__main__":
    main()
