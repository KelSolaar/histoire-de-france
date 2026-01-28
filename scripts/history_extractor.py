# /// script
# dependencies = [
#   "aiofiles",
#   "click",
# ]
# requires-python = ">=3.10"
# ///

"""
Historical Data Extraction Script with LLM Support

A flexible, single-file Python script that extracts historical data
(dates, events, people, dynasties) from text using LLM tools
(Gemini CLI via `gemini -p`). Supports parallel processing.

Adapted from the docstring_processor.py pattern.

Usage:
    uv run scripts/history_extractor.py --tool gemini \\
        --input data/chapters/chapter_01.txt --output data/extracted/
"""

import asyncio
import json
import logging
import re
import signal
import subprocess
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

import aiofiles
import click

from llm_processor import PROCESSORS, AsyncLLMProcessor, setup_logging

# Module-level logger
LOGGER = logging.getLogger(__name__)


# ==============================================================================
# DATA STRUCTURES
# ==============================================================================


class EntryType(Enum):
    """Enumeration of timeline entry types."""

    EVENT = "event"
    BATTLE = "battle"
    TREATY = "treaty"
    REIGN_START = "reign_start"
    REIGN_END = "reign_end"
    BIRTH = "birth"
    DEATH = "death"
    PERIOD = "period"
    RELIGIOUS = "religious"
    POLITICAL = "political"


@dataclass
class DateSpec:
    """Specification for a historical date."""

    year: int
    month: Optional[int] = None
    day: Optional[int] = None
    circa: bool = False
    era: str = "CE"  # "BCE" or "CE"
    precision: str = "year"  # "exact", "year", "decade", "century"


@dataclass
class SourceReference:
    """Reference to source text."""

    chapter: int
    line_start: int
    line_end: int
    excerpt: str


@dataclass
class TimelineEntry:
    """Container for a timeline entry."""

    id: str
    type: str
    date_start: dict
    title: str
    description: str
    source: dict
    importance: int = 3  # 1-5 scale, 5 = most important
    people: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)
    group_dynasty: str = ""
    group_era: str = ""
    date_end: Optional[dict] = None
    image_url: Optional[str] = None
    image_caption: Optional[str] = None


@dataclass
class Person:
    """Container for a historical person."""

    id: str
    name: str
    aliases: list[str] = field(default_factory=list)
    dynasty: Optional[str] = None
    titles: list[str] = field(default_factory=list)
    birth: Optional[dict] = None
    death: Optional[dict] = None
    reign: Optional[dict] = None
    relations: list[dict] = field(default_factory=list)
    image_url: Optional[str] = None
    source_lines: list[int] = field(default_factory=list)


@dataclass
class ExtractionResult:
    """Result of extracting historical data from a chapter."""

    chapter_number: int
    chapter_title: str
    line_start: int
    line_end: int
    entries: list[dict] = field(default_factory=list)
    people: list[dict] = field(default_factory=list)
    dynasties: list[dict] = field(default_factory=list)
    validation_status: str = ""
    errors: list[str] = field(default_factory=list)


# ==============================================================================
# PROMPT TEMPLATES
# ==============================================================================

EXTRACTION_PROMPT = """Tu es un historien expert spécialisé dans l'histoire de France.
Extrais TOUTES les informations historiques de ce chapitre de l'Histoire de France
de Jacques Bainville.

IMPORTANT: Le texte commence à la ligne {line_start} du fichier original.
Tous les numéros de ligne dans tes réponses doivent être relatifs à ce décalage.

RÈGLE CRITIQUE - CLASSIFICATION PAR DYNASTIE/PÉRIODE:
La valeur de "group_dynasty" doit TOUJOURS correspondre à la DATE RÉELLE de l'événement,
PAS au chapitre dans lequel il est mentionné. Les textes historiques font souvent référence
à des événements d'autres époques pour comparaison ou contexte.

Utilise UNIQUEMENT ces valeurs pour group_dynasty (date → période):
- Avant 52 av. J.-C.: "Gaulois"
- 52 av. J.-C. - 481: "Gallo-Romains"
- 481-751: "Mérovingiens"
- 751-987: "Carolingiens"
- 987-1328: "Capétiens"
- 1328-1589: "Valois"
- 1589-1792: "Bourbons"
- 1792-1799: "Première République"
- 1799-1804: "Consulat"
- 1804-1814: "Premier Empire"
- 1814-1830: "Restauration"
- 1830-1848: "Monarchie de Juillet"
- 1848-1852: "Deuxième République"
- 1852-1870: "Second Empire"
- 1870-1940: "Troisième République"

EXEMPLE: Si un chapitre sur la Troisième République mentionne "la bataille de Poitiers en 732",
cet événement doit avoir group_dynasty="Mérovingiens" (car 732 est dans la période 481-751),
PAS "Troisième République" qui est l'époque du chapitre.

Pour chaque ÉVÉNEMENT historique, fournis:
- type: "event" | "battle" | "treaty" | "reign_start" | "reign_end" | "birth" | "death" | "period" | "religious" | "political"
- date_start: {{year: number, month?: number, day?: number, circa: boolean, era: "BCE"|"CE", precision: "exact"|"year"|"decade"|"century"}}
- date_end: (optionnel, pour les périodes)
- title: titre concis en français
- description: description en français (1-2 phrases)
- importance: niveau d'importance de 1 à 5 (voir critères ci-dessous)
- people: liste des noms des personnes impliquées
- locations: liste des lieux mentionnés
- group_dynasty: dynastie déterminée par la DATE de l'événement (voir correspondances ci-dessus)
- source: {{line_start: number, line_end: number, excerpt: "citation du texte (max 200 chars)"}}

CRITÈRES D'IMPORTANCE (1-5):
5 = Événements fondateurs de la nation française
    - Baptême de Clovis, Sacre de Charlemagne, Sacre d'Hugues Capet
    - Traités majeurs (Verdun 843), Révolution française
    - Événements qui ont fondamentalement changé le cours de l'histoire de France
4 = Événements majeurs avec impact durable
    - Grandes batailles décisives (Poitiers 732, Bouvines 1214)
    - Débuts/fins de dynasties majeures
    - Réformes institutionnelles importantes
3 = Événements significatifs régionaux ou temporels
    - Batailles importantes mais non décisives
    - Règnes de rois importants
    - Événements religieux ou politiques notables
2 = Événements mineurs mais documentés
    - Batailles locales, traités mineurs
    - Naissances/morts de personnages secondaires
    - Événements administratifs
1 = Détails historiques de contexte
    - Événements anecdotiques, faits divers
    - Informations de contexte sans impact majeur

Pour chaque PERSONNE mentionnée, fournis:
- name: nom complet
- aliases: autres noms/titres utilisés
- dynasty: dynastie d'appartenance
- titles: titres (roi, empereur, pape, etc.)
- birth: date de naissance si mentionnée
- death: date de mort si mentionnée
- reign: {{start: DateSpec, end: DateSpec}} si applicable
- relations: liste de {{type: "père"|"mère"|"fils"|"fille"|"époux"|"épouse", person: "nom"}}
- source_lines: lignes où la personne est mentionnée

Réponds UNIQUEMENT avec un JSON valide dans ce format exact:
```json
{{
  "entries": [...],
  "people": [...],
  "dynasties": [
    {{
      "id": "dyn_xxx",
      "name": "Nom de la dynastie",
      "period_start": DateSpec,
      "period_end": DateSpec
    }}
  ]
}}
```

Chapitre {chapter_number}: {chapter_title}
Lignes {line_start} à {line_end}

---
{chapter_text}
---
"""


# ==============================================================================
# EXTRACTION LOGIC
# ==============================================================================


def _parse_json_response(
    response: str,
    chapter_number: int,
    chapter_title: str,
    line_start: int,
    line_end: int,
) -> ExtractionResult:
    """Parse JSON from LLM response."""
    # Try to find JSON block in response
    json_match = re.search(r"```json\s*\n(.*?)\n```", response, re.DOTALL)
    if json_match:
        json_str = json_match.group(1)
    else:
        # Try to find raw JSON
        json_match = re.search(r"\{[\s\S]*\}", response)
        if json_match:
            json_str = json_match.group(0)
        else:
            raise ValueError("No JSON found in response")

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid JSON: {error}")

    # Generate IDs for entries
    entries = data.get("entries", [])
    for i, entry in enumerate(entries):
        if "id" not in entry:
            entry["id"] = f"evt_{chapter_number}_{i + 1:03d}"

    # Generate IDs for people
    people = data.get("people", [])
    for i, person in enumerate(people):
        if "id" not in person:
            name_slug = re.sub(
                r"[^a-z0-9]", "_", person.get("name", "unknown").lower()
            )[:20]
            person["id"] = f"per_{name_slug}"

    return ExtractionResult(
        chapter_number=chapter_number,
        chapter_title=chapter_title,
        line_start=line_start,
        line_end=line_end,
        entries=entries,
        people=people,
        dynasties=data.get("dynasties", []),
        validation_status="success",
    )


async def extract_history(
    processor: AsyncLLMProcessor,
    chapter_text: str,
    chapter_number: int,
    chapter_title: str,
    line_start: int,
    line_end: int,
) -> ExtractionResult:
    """Extract historical data from chapter text using an LLM processor."""
    LOGGER.info(
        "[Chapter %s] Starting extraction with %s",
        chapter_number,
        processor.tool_name,
    )

    prompt = EXTRACTION_PROMPT.format(
        chapter_number=chapter_number,
        chapter_title=chapter_title,
        chapter_text=chapter_text,
        line_start=line_start,
        line_end=line_end,
    )

    LOGGER.debug(
        "[Chapter %s] Prompt length: %s chars",
        chapter_number,
        len(prompt),
    )

    try:
        response = await processor.process(prompt)
        return _parse_json_response(
            response, chapter_number, chapter_title, line_start, line_end
        )
    except Exception as error:
        LOGGER.error(
            "[Chapter %s] Extraction failed: %s",
            chapter_number,
            str(error)[:500],
        )
        return ExtractionResult(
            chapter_number=chapter_number,
            chapter_title=chapter_title,
            line_start=line_start,
            line_end=line_end,
            validation_status="error",
            errors=[str(error)],
        )


# ==============================================================================
# FILE PROCESSING
# ==============================================================================


def parse_chapter_file(file_path: Path) -> tuple[int, str, int, int, str]:
    """
    Parse a chapter file and extract metadata from header.

    Returns
    -------
    tuple
        (chapter_number, chapter_title, line_start, line_end, content)
    """
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    lines = content.split("\n")

    # Parse header: # Lines {start}-{end} from {source}
    header_match = re.match(r"# Lines (\d+)-(\d+) from", lines[0])
    if header_match:
        line_start = int(header_match.group(1))
        line_end = int(header_match.group(2))
        # Skip header line
        content = "\n".join(lines[2:])
    else:
        line_start = 1
        line_end = len(lines)

    # Extract chapter number from filename
    filename_match = re.search(r"chapter_(\d+)", file_path.stem)
    chapter_number = int(filename_match.group(1)) if filename_match else 0

    # Extract chapter title from content
    title_match = re.search(r"^CHAPITRE.*?\n+(.+?)(?:\n\n|\n$)", content, re.MULTILINE)
    if title_match:
        chapter_title = title_match.group(1).strip()
    else:
        chapter_title = f"Chapter {chapter_number}"

    return chapter_number, chapter_title, line_start, line_end, content


async def process_chapter_file(
    file_path: Path,
    processor: AsyncLLMProcessor,
    output_dir: Path,
) -> ExtractionResult:
    """Process a single chapter file."""
    LOGGER.info("Processing: %s", file_path.name)

    chapter_number, chapter_title, line_start, line_end, content = parse_chapter_file(
        file_path
    )

    LOGGER.info(
        "Chapter %s: %s (lines %s-%s)",
        chapter_number,
        chapter_title[:50],
        line_start,
        line_end,
    )

    result = await extract_history(
        processor, content, chapter_number, chapter_title, line_start, line_end
    )

    # Write result to output file
    output_file = output_dir / ("chapter_%02d.json" % chapter_number)
    output_dir.mkdir(parents=True, exist_ok=True)

    output_data = {
        "chapter_number": result.chapter_number,
        "chapter_title": result.chapter_title,
        "line_start": result.line_start,
        "line_end": result.line_end,
        "validation_status": result.validation_status,
        "entries": result.entries,
        "people": result.people,
        "dynasties": result.dynasties,
        "errors": result.errors,
    }

    async with aiofiles.open(output_file, "w", encoding="utf-8") as f:
        await f.write(json.dumps(output_data, ensure_ascii=False, indent=2))

    LOGGER.info(
        "Chapter %s: extracted %s entries, %s people",
        chapter_number,
        len(result.entries),
        len(result.people),
    )

    return result


# ==============================================================================
# MAIN
# ==============================================================================


async def _async_main(
    input_path: Path,
    output_dir: Path,
    tool: str,
    parallel: int,
):
    """Async main function."""

    # Signal handlers
    def signal_handler(sig, frame):
        LOGGER.info("Received signal %s, shutting down...", sig)
        for task in asyncio.all_tasks():
            if not task.done():
                task.cancel()

    if sys.platform != "win32":
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

    # Validate tool
    try:
        subprocess.run([tool, "--version"], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        LOGGER.error("Error: %s CLI is not available", tool)
        sys.exit(1)

    # Create processor
    processor = PROCESSORS[tool]()
    LOGGER.info("Using %s processor", tool)

    # Find input files
    if input_path.is_file():
        files = [input_path]
    else:
        files = sorted(input_path.glob("chapter_*.txt"))

    if not files:
        LOGGER.error("No chapter files found in %s", input_path)
        sys.exit(1)

    LOGGER.info("Found %s chapter files", len(files))

    # Process files
    semaphore = asyncio.Semaphore(parallel)

    async def process_with_semaphore(file_path: Path) -> ExtractionResult:
        async with semaphore:
            return await process_chapter_file(file_path, processor, output_dir)

    tasks = [process_with_semaphore(f) for f in files]

    try:
        results = await asyncio.gather(*tasks, return_exceptions=True)
    except asyncio.CancelledError:
        LOGGER.info("Processing cancelled")
        return

    # Summary
    success_count = sum(
        1
        for r in results
        if isinstance(r, ExtractionResult) and r.validation_status == "success"
    )
    error_count = len(results) - success_count

    LOGGER.info("\n" + "=" * 60)
    LOGGER.info("EXTRACTION SUMMARY")
    LOGGER.info("=" * 60)
    LOGGER.info("Files processed: %s", len(files))
    LOGGER.info("Successful: %s", success_count)
    LOGGER.info("Errors: %s", error_count)

    total_entries = 0
    total_people = 0
    for r in results:
        if isinstance(r, ExtractionResult):
            total_entries += len(r.entries)
            total_people += len(r.people)

    LOGGER.info("Total entries extracted: %s", total_entries)
    LOGGER.info("Total people extracted: %s", total_people)
    LOGGER.info("=" * 60)


@click.command()
@click.option(
    "--input",
    "-i",
    "input_path",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help="Input chapter file or directory containing chapter files.",
)
@click.option(
    "--output",
    "-o",
    "output_dir",
    type=click.Path(path_type=Path),
    required=True,
    help="Output directory for extracted JSON files.",
)
@click.option(
    "--tool",
    "-t",
    type=click.Choice(["gemini", "claude"]),
    default="gemini",
    help="LLM tool to use (default: gemini).",
)
@click.option(
    "--parallel",
    "-p",
    type=int,
    default=1,
    help="Number of parallel extractions (default: 1).",
)
def main(
    input_path: Path,
    output_dir: Path,
    tool: str,
    parallel: int,
):
    """
    Extract historical data from chapter files using LLM.

    Examples:

        # Process single chapter with Gemini
        uv run history_extractor.py -i data/chapters/chapter_01.txt \\
            -o data/extracted/ -t gemini

        # Process all chapters in parallel
        uv run history_extractor.py -i data/chapters/ -o data/extracted/ \\
            -t gemini -p 4
    """
    setup_logging()

    LOGGER.info("Starting history extraction")
    LOGGER.info("Tool: %s, Parallel: %s", tool, parallel)

    try:
        asyncio.run(_async_main(input_path, output_dir, tool, parallel))
    except KeyboardInterrupt:
        LOGGER.info("Interrupted by user")
        sys.exit(130)
    except asyncio.CancelledError:
        LOGGER.info("Cancelled")
        sys.exit(1)


if __name__ == "__main__":
    main()
