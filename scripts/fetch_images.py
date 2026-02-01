# /// script
# dependencies = [
#   "aiohttp",
#   "click",
# ]
# ///
"""
Fetch Wikipedia Images Script

Fetches images from French Wikipedia for timeline entries.
Searches both people and event titles, picks the best match,
and stores the image URL and attribution.

Usage:
    uv run scripts/fetch_images.py
    uv run scripts/fetch_images.py --input data/timeline_entries.json --concurrency 10
"""

import asyncio
import json
import logging
import re
import signal
import sys
from html import unescape
from pathlib import Path

import aiohttp
import click

# ==============================================================================
# CONFIGURATION
# ==============================================================================

LOGGER = logging.getLogger(__name__)

FR_WIKI_API = "https://fr.wikipedia.org/w/api.php"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
USER_AGENT = "HistoireDeFranceBot/1.0 (https://github.com/KelSolaar/histoire-de-france)"

MAX_RETRIES = 5
BASE_DELAY = 2.0


# ==============================================================================
# WIKIPEDIA API FUNCTIONS
# ==============================================================================


async def _get_json(
    session: aiohttp.ClientSession, url: str, params: dict, label: str
) -> dict | None:
    """GET with retry and exponential backoff on 429."""

    for attempt in range(MAX_RETRIES):
        try:
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    return await resp.json()
                if resp.status == 429:
                    delay = BASE_DELAY * (2**attempt)
                    LOGGER.debug("429 for '%s', retrying in %.1fs", label, delay)
                    await asyncio.sleep(delay)
                    continue
                LOGGER.warning("HTTP %s for '%s'", resp.status, label)
                return None
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
            LOGGER.warning("Request error for '%s': %s", label, e)
            return None

    LOGGER.warning("Max retries for '%s'", label)
    return None


async def search_wikipedia(
    session: aiohttp.ClientSession, query: str
) -> str | None:
    """Search French Wikipedia for an article matching the query."""

    params = {
        "action": "query",
        "list": "search",
        "srsearch": query,
        "format": "json",
        "srlimit": 1,
    }

    data = await _get_json(session, FR_WIKI_API, params, query)
    if not data:
        return None
    results = data.get("query", {}).get("search", [])
    return results[0]["title"] if results else None


async def get_page_image(
    session: aiohttp.ClientSession, page_title: str, thumb_size: int
) -> tuple[str, str] | None:
    """Get the main thumbnail URL and filename for a Wikipedia page."""

    params = {
        "action": "query",
        "titles": page_title,
        "prop": "pageimages",
        "format": "json",
        "pithumbsize": thumb_size,
        "pilicense": "any",
    }

    data = await _get_json(session, FR_WIKI_API, params, page_title)
    if not data:
        return None
    pages = data.get("query", {}).get("pages", {})
    for page in pages.values():
        thumb = page.get("thumbnail", {})
        thumb_url = thumb.get("source")
        filename = page.get("pageimage")
        if thumb_url and filename:
            return (thumb_url, filename)
    return None


def _strip_html(text: str) -> str:
    """Remove HTML tags and unescape entities."""

    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return unescape(text).strip()


async def get_image_attribution(
    session: aiohttp.ClientSession, filename: str
) -> str:
    """Get attribution string for an image from Wikimedia Commons."""

    params = {
        "action": "query",
        "titles": f"File:{filename}",
        "prop": "imageinfo",
        "iiprop": "extmetadata",
        "format": "json",
    }

    data = await _get_json(session, COMMONS_API, params, filename)
    if not data:
        return "Wikimedia Commons"

    pages = data.get("query", {}).get("pages", {})
    for page in pages.values():
        imageinfo = page.get("imageinfo", [])
        if not imageinfo:
            return "Wikimedia Commons"
        metadata = imageinfo[0].get("extmetadata", {})

        artist = metadata.get("Artist", {}).get("value", "")
        artist = _strip_html(artist).strip()

        license_name = metadata.get("LicenseShortName", {}).get("value", "")

        parts = []
        if artist:
            parts.append(artist)
        if license_name:
            parts.append(license_name)

        return " — ".join(parts) if parts else "Wikimedia Commons"

    return "Wikimedia Commons"


# ==============================================================================
# ENTRY PROCESSING
# ==============================================================================


async def fetch_image_for_entry(
    session: aiohttp.ClientSession,
    entry: dict,
    semaphore: asyncio.Semaphore,
    thumb_size: int,
) -> dict:
    """Fetch the best image for a single timeline entry."""

    async with semaphore:
        entry_id = entry["id"]
        people = entry.get("people", [])
        title = entry.get("title", "")

        # Build search queries: people first (preferred), then title
        queries = []
        for person in people:
            queries.append(("people", person))
        queries.append(("title", title))

        # Search all queries, collect results
        people_result = None
        title_result = None

        for query_type, query in queries:
            if not query:
                continue

            page_title = await search_wikipedia(session, query)
            if not page_title:
                continue

            image = await get_page_image(session, page_title, thumb_size)
            if not image:
                continue

            if query_type == "people" and not people_result:
                people_result = image
            elif query_type == "title" and not title_result:
                title_result = image

            # Stop early if we have a people image (preferred)
            if people_result:
                break

        # Pick best: prefer people over title
        best = people_result or title_result

        if best:
            thumb_url, filename = best
            attribution = await get_image_attribution(session, filename)
            entry["image_url"] = thumb_url
            entry["image_attribution"] = attribution
            LOGGER.info(
                "%s: found image (%s)",
                entry_id,
                filename,
            )
        else:
            LOGGER.debug("%s: no image found", entry_id)

        return entry


# ==============================================================================
# MAIN
# ==============================================================================


async def _async_main(
    input_path: Path,
    output_path: Path,
    concurrency: int,
    thumb_size: int,
    skip_existing: bool,
) -> None:
    """Main async processing loop."""

    shutdown = False

    def handle_signal(sig, _frame):
        nonlocal shutdown
        LOGGER.warning("Received signal %s, finishing current requests...", sig)
        shutdown = True

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    with open(input_path, encoding="utf-8") as f:
        entries = json.load(f)

    LOGGER.info("Loaded %s entries from %s", len(entries), input_path)

    semaphore = asyncio.Semaphore(concurrency)

    headers = {"User-Agent": USER_AGENT}
    async with aiohttp.ClientSession(headers=headers) as session:
        total_fetched = 0
        total_skipped = 0
        total_errors = 0

        # Process in chunks to allow graceful shutdown
        chunk_size = concurrency * 2
        for i in range(0, len(entries), chunk_size):
            if shutdown:
                LOGGER.warning("Shutdown requested, stopping.")
                break

            chunk = entries[i : i + chunk_size]
            tasks = []

            for entry in chunk:
                if skip_existing and entry.get("image_url"):
                    total_skipped += 1
                    continue
                tasks.append(
                    fetch_image_for_entry(session, entry, semaphore, thumb_size)
                )

            if tasks:
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for result in results:
                    if isinstance(result, BaseException):
                        total_errors += 1
                        LOGGER.warning("Fetch error: %s", result)
                await asyncio.sleep(1.0)

            for entry in chunk:
                if entry.get("image_url"):
                    total_fetched += 1

            LOGGER.info(
                "Progress: %s/%s entries processed",
                min(i + chunk_size, len(entries)),
                len(entries),
            )

    # Write output
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)

    LOGGER.info("Wrote entries to %s", output_path)
    LOGGER.info("=== Image Fetch Summary ===")
    LOGGER.info("Total with images: %s / %s", total_fetched, len(entries))
    if total_skipped:
        LOGGER.info("Skipped (existing): %s", total_skipped)
    if total_errors:
        LOGGER.warning("Errors: %s", total_errors)


# ==============================================================================
# CLI
# ==============================================================================


def setup_logging(log_level: int = logging.INFO) -> logging.Logger:
    """Configure logging."""

    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)33s - %(levelname)8s - %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
        ],
    )

    return LOGGER


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
    "--concurrency",
    "-c",
    default=3,
    type=int,
    help="Max concurrent requests",
)
@click.option(
    "--thumb-size",
    "-s",
    default=400,
    type=int,
    help="Thumbnail width in pixels",
)
@click.option(
    "--skip-existing",
    is_flag=True,
    help="Skip entries that already have an image",
)
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging")
def main(input_path, output_path, concurrency, thumb_size, skip_existing, verbose):
    """Fetch Wikipedia images for timeline entries."""

    setup_logging()
    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    input_path = Path(input_path)
    output_path = Path(output_path) if output_path else input_path

    LOGGER.info("Fetching images for entries in %s", input_path)
    LOGGER.info("Concurrency: %s, thumb size: %spx", concurrency, thumb_size)

    asyncio.run(
        _async_main(input_path, output_path, concurrency, thumb_size, skip_existing)
    )


if __name__ == "__main__":
    main()
