# /// script
# dependencies = [
#   "click",
# ]
# ///
"""
Tag Entries Script

Assigns predefined tags to timeline entries using an LLM.
Reads merged timeline_entries.json, sends batches to the LLM,
and writes tagged entries back.

Usage:
    uv run scripts/tag_entries.py --tool gemini
    uv run scripts/tag_entries.py --input data/timeline_entries.json --tool claude
"""

import asyncio
import json
import logging
import re
import signal
import sys
from collections import Counter
from pathlib import Path

import click

from llm_processor import PROCESSORS, setup_logging

# ==============================================================================
# CONFIGURATION
# ==============================================================================

LOGGER = logging.getLogger(__name__)

TAGS = [
    "Alliance",
    "Assassinat",
    "Bataille",
    "Conquête",
    "Couronnement",
    "Croisade",
    "Culture",
    "Diplomatie",
    "Fondation",
    "Guerre",
    "Loi",
    "Mort",
    "Politique",
    "Réforme",
    "Régence",
    "Révolution",
    "Révolte",
    "Religion",
    "Siège",
    "Succession",
    "Traité",
    "Économie",
]

TAG_SET = frozenset(TAGS)

TAG_PROMPT = """\
Tu es un historien expert spécialisé dans l'histoire de France.
Assigne 1 à 4 tags à chaque événement historique ci-dessous.

TAGS DISPONIBLES (utilise UNIQUEMENT ces tags, orthographe exacte):
{tag_list}

Retourne UNIQUEMENT un objet JSON (sans commentaires, sans markdown) où chaque clé
est l'identifiant de l'événement et la valeur est un tableau de tags:
{{"evt_0001": ["Bataille", "Conquête"], "evt_0002": ["Politique", "Réforme"]}}

ÉVÉNEMENTS:
{entries_block}
"""


# ==============================================================================
# PROCESSING FUNCTIONS
# ==============================================================================


def format_entry_for_prompt(entry: dict) -> str:
    """Format a single entry as a compact line for the LLM prompt."""

    entry_id = entry["id"]
    entry_type = entry.get("type", "event")
    date = entry.get("date_start", {})
    year = date.get("year", "?")
    if date.get("era") == "BCE":
        year = f"-{abs(year)}" if isinstance(year, int) else year
    title = entry.get("title", "")
    description = entry.get("description", "")
    if len(description) > 120:
        description = description[:117] + "..."

    return f"- {entry_id} | {entry_type} | {year} | {title} | {description}"


def parse_tag_response(
    response: str, entry_ids: list[str]
) -> dict[str, list[str]]:
    """Parse LLM response into a mapping of entry IDs to tag lists."""

    # Try markdown fence first
    json_match = re.search(r"```json\s*\n(.*?)\n```", response, re.DOTALL)
    if json_match:
        json_str = json_match.group(1)
    else:
        # Try raw JSON object
        json_match = re.search(r"\{[\s\S]*\}", response)
        if json_match:
            json_str = json_match.group(0)
        else:
            raise ValueError("No JSON found in LLM response")

    data = json.loads(json_str)

    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object, got {type(data).__name__}")

    result = {}
    id_set = set(entry_ids)

    for entry_id, tags in data.items():
        if entry_id not in id_set:
            LOGGER.warning("Unknown entry ID in response: %s", entry_id)
            continue

        if not isinstance(tags, list):
            LOGGER.warning(
                "Expected list for %s, got %s", entry_id, type(tags).__name__
            )
            continue

        valid_tags = []
        for tag in tags:
            if tag in TAG_SET:
                valid_tags.append(tag)
            else:
                LOGGER.warning(
                    "Unknown tag '%s' for %s, skipping", tag, entry_id
                )

        if valid_tags:
            result[entry_id] = sorted(valid_tags)

    return result


async def tag_batch(
    processor, entries: list[dict]
) -> dict[str, list[str]]:
    """Tag a batch of entries using the LLM."""

    entries_block = "\n".join(
        format_entry_for_prompt(entry) for entry in entries
    )
    tag_list = ", ".join(TAGS)

    prompt = TAG_PROMPT.format(tag_list=tag_list, entries_block=entries_block)
    entry_ids = [e["id"] for e in entries]

    response = await processor.process(prompt)

    return parse_tag_response(response, entry_ids)


async def _async_main(
    input_path: Path,
    output_path: Path,
    tool: str,
    batch_size: int,
) -> None:
    """Main async processing loop."""

    shutdown = False

    def handle_signal(sig, _frame):
        nonlocal shutdown
        LOGGER.warning("Received signal %s, finishing current batch...", sig)
        shutdown = True

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    # Load entries
    with open(input_path, encoding="utf-8") as f:
        entries = json.load(f)

    LOGGER.info("Loaded %s entries from %s", len(entries), input_path)

    # Build ID → entry index map
    id_to_index = {e["id"]: i for i, e in enumerate(entries)}

    # Process in batches
    processor = PROCESSORS[tool]()
    total_tagged = 0
    total_failed = 0
    all_tags = Counter()

    batches = [
        entries[i : i + batch_size]
        for i in range(0, len(entries), batch_size)
    ]

    for batch_num, batch in enumerate(batches, 1):
        if shutdown:
            LOGGER.warning("Shutdown requested, stopping after batch %s", batch_num - 1)
            break

        LOGGER.info(
            "Processing batch %s/%s (%s entries)...",
            batch_num,
            len(batches),
            len(batch),
        )

        try:
            tag_map = await tag_batch(processor, batch)

            for entry_id, tags in tag_map.items():
                idx = id_to_index[entry_id]
                entries[idx]["tags"] = tags
                all_tags.update(tags)
                total_tagged += 1

            LOGGER.info(
                "Batch %s: tagged %s/%s entries",
                batch_num,
                len(tag_map),
                len(batch),
            )

        except Exception as error:
            total_failed += len(batch)
            LOGGER.error(
                "Batch %s failed: %s", batch_num, str(error)[:300]
            )

    # Write output
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)

    LOGGER.info("Wrote tagged entries to %s", output_path)

    # Summary
    LOGGER.info("=== Tagging Summary ===")
    LOGGER.info("Total tagged: %s / %s", total_tagged, len(entries))
    if total_failed:
        LOGGER.warning("Total failed: %s", total_failed)
    LOGGER.info("Tag distribution:")
    for tag, count in all_tags.most_common():
        LOGGER.info("  %-20s %s", tag, count)


# ==============================================================================
# CLI ENTRY POINT
# ==============================================================================


@click.command()
@click.option(
    "--input",
    "-i",
    "input_path",
    default="data/timeline_entries.json",
    type=click.Path(exists=True),
    help="Path to timeline_entries.json",
)
@click.option(
    "--output",
    "-o",
    "output_path",
    default=None,
    type=click.Path(),
    help="Output path (default: same as input)",
)
@click.option(
    "--tool",
    "-t",
    default="gemini",
    type=click.Choice(list(PROCESSORS.keys())),
    help="LLM tool to use",
)
@click.option(
    "--batch-size",
    "-b",
    default=25,
    type=int,
    help="Entries per LLM batch",
)
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging")
def main(input_path, output_path, tool, batch_size, verbose):
    """Assign predefined tags to timeline entries using an LLM."""

    setup_logging()
    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    input_path = Path(input_path)
    output_path = Path(output_path) if output_path else input_path

    LOGGER.info("Tagging entries from %s using %s", input_path, tool)
    LOGGER.info("Batch size: %s, output: %s", batch_size, output_path)

    asyncio.run(_async_main(input_path, output_path, tool, batch_size))


if __name__ == "__main__":
    main()
