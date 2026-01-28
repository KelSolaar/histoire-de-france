# /// script
# dependencies = [
#   "click",
# ]
# requires-python = ">=3.10"
# ///

"""
Data Merger Script

Combines extracted chapter data into unified JSON files for the timeline.
Deduplicates people, merges dynasties, and creates group definitions.

Usage:
    uv run scripts/data_merger.py --input data/extracted/ --output data/
"""

import json
import logging
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import click

# ==============================================================================
# CONFIGURATION
# ==============================================================================


@dataclass
class MergerConfig:
    """Configuration settings for data merging."""

    # Dynasty colors for visualization
    dynasty_colors: dict[str, str] = field(default_factory=lambda: {
        "Gaulois": "#8B4513",
        "Gallo-Romains": "#CD853F",
        "Mérovingiens": "#4169E1",
        "Carolingiens": "#DAA520",
        "Capétiens": "#4682B4",
        "Valois": "#9370DB",
        "Bourbon": "#DC143C",
        "Bonaparte": "#2F4F4F",
        "Orléans": "#FF8C00",
        "République": "#228B22",
        "Wisigoths": "#708090",
        "Burgondes": "#A0522D",
        "Huns": "#800000",
        "Féodaux": "#696969",
        "default": "#808080",
    })


# Module-level logger
LOGGER = logging.getLogger(__name__)


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


def normalize_name(name: str) -> str:
    """Normalize a name for deduplication."""
    # Remove accents and lowercase
    name = name.lower().strip()
    # Remove common prefixes
    for prefix in ["saint ", "sainte ", "le ", "la ", "l'"]:
        if name.startswith(prefix):
            name = name[len(prefix):]
    return name


def merge_person_records(records: list[dict]) -> dict:
    """Merge multiple records for the same person."""
    if not records:
        return {}

    merged = records[0].copy()

    for record in records[1:]:
        # Merge aliases
        if "aliases" in record:
            existing = set(merged.get("aliases", []))
            existing.update(record["aliases"])
            merged["aliases"] = list(existing)

        # Merge titles
        if "titles" in record:
            existing = set(merged.get("titles", []))
            existing.update(record["titles"])
            merged["titles"] = list(existing)

        # Merge source_lines
        if "source_lines" in record:
            existing = set(merged.get("source_lines", []))
            existing.update(record["source_lines"])
            merged["source_lines"] = sorted(list(existing))

        # Merge relations
        if "relations" in record:
            existing_rels = {
                (r.get("type"), r.get("person")): r
                for r in merged.get("relations", [])
            }
            for rel in record["relations"]:
                key = (rel.get("type"), rel.get("person"))
                if key not in existing_rels:
                    existing_rels[key] = rel
            merged["relations"] = list(existing_rels.values())

        # Take non-null values for dates
        for date_field in ["birth", "death", "reign"]:
            if record.get(date_field) and not merged.get(date_field):
                merged[date_field] = record[date_field]

        # Take dynasty if not set
        if record.get("dynasty") and not merged.get("dynasty"):
            merged["dynasty"] = record["dynasty"]

    return merged


# ==============================================================================
# CORE CLASSES
# ==============================================================================


class DataMerger:
    """Merges extracted chapter data into unified files."""

    def __init__(self, config: Optional[MergerConfig] = None):
        self.config = config or MergerConfig()
        self.logger = logging.getLogger(
            f"{__name__}.{self.__class__.__name__}"
        )

    def load_chapter_files(self, input_dir: Path) -> list[dict]:
        """Load all chapter JSON files."""
        files = sorted(input_dir.glob("chapter_*.json"))
        self.logger.info("Found %s chapter files", len(files))

        chapters = []
        for file_path in files:
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("validation_status") == "success":
                    chapters.append(data)
                    self.logger.debug("Loaded: %s", file_path.name)
                else:
                    self.logger.warning(
                        "Skipping %s (status: %s)",
                        file_path.name,
                        data.get("validation_status"),
                    )
            except (json.JSONDecodeError, OSError) as error:
                self.logger.error("Failed to load %s: %s", file_path, error)

        return chapters

    @staticmethod
    def _normalize_title(title: str) -> str:
        """Normalize a title for dedup comparison."""
        title = title.strip().lower()
        # Strip "(référence)" / "(reference)" suffixes
        title = re.sub(r"\s*\(r[ée]f[ée]rence\)\s*$", "", title)
        return title

    @staticmethod
    def _entry_year(entry: dict) -> int:
        """Get chronological year from entry (negative for BCE)."""
        ds = entry.get("date_start", {})
        year = ds.get("year", 0)
        if ds.get("era") == "BCE":
            year = -abs(year)
        return year

    @staticmethod
    def _entry_score(entry: dict) -> tuple:
        """Score an entry for winner selection (higher = better)."""
        return (
            entry.get("importance", 3),
            len(entry.get("people", [])),
            len(entry.get("locations", [])),
            len(entry.get("description", "")),
        )

    def _deduplicate_entries(self, entries: list[dict]) -> list[dict]:
        """Remove duplicate entries using exact match + substring containment."""
        # Phase 1: Group by (normalized_title, year) for exact matches
        groups: dict[tuple, list[dict]] = {}
        for entry in entries:
            title = self._normalize_title(entry.get("title", ""))
            year = self._entry_year(entry)
            key = (title, year)
            groups.setdefault(key, []).append(entry)

        # Pick winner from each exact-match group
        deduped = []
        removed_exact = 0
        for key, group in groups.items():
            winner = max(group, key=self._entry_score)
            deduped.append(winner)
            if len(group) > 1:
                removed_exact += len(group) - 1
                self.logger.debug(
                    "Exact dedup: kept 1 of %s for '%s' (%s)",
                    len(group), key[0], key[1],
                )

        # Phase 2: Substring containment within same year
        by_year: dict[int, list[dict]] = {}
        for entry in deduped:
            year = self._entry_year(entry)
            by_year.setdefault(year, []).append(entry)

        final = []
        removed_substring = 0
        for year, year_entries in by_year.items():
            # Sort by title length descending so longer titles are checked first
            year_entries.sort(
                key=lambda e: len(self._normalize_title(e.get("title", ""))),
                reverse=True,
            )
            kept: list[dict] = []
            for entry in year_entries:
                title = self._normalize_title(entry.get("title", ""))
                # Check if this title is contained in an already-kept entry
                absorbed = False
                for kept_entry in kept:
                    kept_title = self._normalize_title(kept_entry.get("title", ""))
                    if title != kept_title and title in kept_title:
                        # Shorter title is a substring of a kept title
                        # Keep the one with better score
                        if self._entry_score(entry) > self._entry_score(kept_entry):
                            kept.remove(kept_entry)
                            kept.append(entry)
                            self.logger.debug(
                                "Substring dedup: '%s' absorbed by '%s' (%s)",
                                kept_entry.get("title"), entry.get("title"), year,
                            )
                        else:
                            self.logger.debug(
                                "Substring dedup: '%s' absorbed by '%s' (%s)",
                                entry.get("title"), kept_entry.get("title"), year,
                            )
                        absorbed = True
                        removed_substring += 1
                        break
                if not absorbed:
                    kept.append(entry)
            final.extend(kept)

        total_removed = removed_exact + removed_substring
        self.logger.info(
            "Deduplicated: removed %s entries (%s exact, %s substring; %s remain)",
            total_removed, removed_exact, removed_substring, len(final),
        )
        return final

    def merge_entries(self, chapters: list[dict]) -> list[dict]:
        """Merge timeline entries from all chapters."""
        all_entries = []

        for chapter in chapters:
            chapter_num = chapter.get("chapter_number", 0)
            chapter_title = chapter.get("chapter_title", "")

            for entry in chapter.get("entries", []):
                # Add chapter info to source
                if "source" in entry:
                    entry["source"]["chapter"] = chapter_num

                # Set group_era to chapter title
                if not entry.get("group_era"):
                    entry["group_era"] = chapter_title

                # Set default dynasty if missing
                if not entry.get("group_dynasty"):
                    entry["group_dynasty"] = "Non classé"

                all_entries.append(entry)

        self.logger.info("Collected %s timeline entries", len(all_entries))

        # Deduplicate
        all_entries = self._deduplicate_entries(all_entries)

        # Re-assign sequential IDs
        for i, entry in enumerate(all_entries, start=1):
            entry["id"] = f"evt_{i:04d}"

        return all_entries

    def merge_people(self, chapters: list[dict]) -> list[dict]:
        """Merge and deduplicate people from all chapters."""
        # Group by normalized name
        people_by_name: dict[str, list[dict]] = defaultdict(list)

        for chapter in chapters:
            for person in chapter.get("people", []):
                name = person.get("name", "")
                key = normalize_name(name)
                people_by_name[key].append(person)

        # Merge records
        merged_people = []
        person_id = 1

        for key, records in people_by_name.items():
            merged = merge_person_records(records)
            merged["id"] = f"per_{person_id:04d}"
            person_id += 1
            merged_people.append(merged)

        self.logger.info(
            "Merged %s people (from %s records)",
            len(merged_people),
            sum(len(p.get("people", [])) for p in chapters),
        )

        return merged_people

    def merge_dynasties(self, chapters: list[dict]) -> list[dict]:
        """Merge and deduplicate dynasties from all chapters."""
        dynasties_by_name: dict[str, list[dict]] = defaultdict(list)

        for chapter in chapters:
            for dynasty in chapter.get("dynasties", []):
                name = dynasty.get("name", "")
                dynasties_by_name[name].append(dynasty)

        merged_dynasties = []
        dynasty_id = 1

        for name, records in dynasties_by_name.items():
            # Take the first record as base, could be smarter about merging
            merged = records[0].copy()
            merged["id"] = f"dyn_{dynasty_id:04d}"
            dynasty_id += 1

            # Add color
            merged["color"] = self.config.dynasty_colors.get(
                name, self.config.dynasty_colors["default"]
            )

            merged_dynasties.append(merged)

        self.logger.info("Merged %s dynasties", len(merged_dynasties))
        return merged_dynasties

    def create_groups(
        self,
        entries: list[dict],
        dynasties: list[dict],
        chapters: list[dict],
    ) -> dict:
        """Create group definitions for the timeline."""
        groups = {
            "dynasty": [],
            "era": [],
        }

        # Dynasty groups
        dynasty_names = set()
        for entry in entries:
            if entry.get("group_dynasty"):
                dynasty_names.add(entry["group_dynasty"])

        for i, name in enumerate(sorted(dynasty_names)):
            groups["dynasty"].append({
                "id": f"grp_dyn_{i}",
                "label": name,
                "type": "dynasty",
                "order": i,
                "color": self.config.dynasty_colors.get(
                    name, self.config.dynasty_colors["default"]
                ),
            })

        # Era groups (chapters)
        for i, chapter in enumerate(chapters):
            title = chapter.get("chapter_title", f"Chapter {i + 1}")
            groups["era"].append({
                "id": f"grp_era_{i}",
                "label": title,
                "type": "era",
                "order": i,
                "chapter": chapter.get("chapter_number", i + 1),
            })

        self.logger.info(
            "Created groups: %s dynasties, %s eras",
            len(groups["dynasty"]),
            len(groups["era"]),
        )

        return groups

    def merge_all(self, input_dir: Path, output_dir: Path) -> dict:
        """Run the complete merge process."""
        output_dir.mkdir(parents=True, exist_ok=True)

        # Load chapters
        chapters = self.load_chapter_files(input_dir)
        if not chapters:
            self.logger.error("No valid chapter files found")
            return {"error": "No chapters found"}

        # Merge data
        entries = self.merge_entries(chapters)
        people = self.merge_people(chapters)
        dynasties = self.merge_dynasties(chapters)
        groups = self.create_groups(entries, dynasties, chapters)

        # Write output files
        output_files = {
            "timeline_entries.json": entries,
            "people.json": people,
            "dynasties.json": dynasties,
            "groups.json": groups,
        }

        for filename, data in output_files.items():
            output_path = output_dir / filename
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self.logger.info("Wrote: %s", output_path)

        return {
            "entries": len(entries),
            "people": len(people),
            "dynasties": len(dynasties),
            "groups": sum(len(g) for g in groups.values()),
        }


# ==============================================================================
# MAIN
# ==============================================================================


@click.command()
@click.option(
    "--input",
    "-i",
    "input_dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    required=True,
    help="Input directory containing extracted chapter JSON files.",
)
@click.option(
    "--output",
    "-o",
    "output_dir",
    type=click.Path(path_type=Path),
    required=True,
    help="Output directory for merged JSON files.",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    default=False,
    help="Enable verbose output.",
)
def main(input_dir: Path, output_dir: Path, verbose: bool):
    """
    Merge extracted chapter data into unified timeline files.

    Examples:

        uv run data_merger.py -i data/extracted/ -o data/
    """
    setup_logging(logging.DEBUG if verbose else logging.INFO)

    LOGGER.info("Merging data from %s to %s", input_dir, output_dir)

    merger = DataMerger()
    result = merger.merge_all(input_dir, output_dir)

    if "error" in result:
        LOGGER.error("Merge failed: %s", result["error"])
        sys.exit(1)

    # Summary
    click.echo("\n" + "=" * 60)
    click.echo("MERGE SUMMARY")
    click.echo("=" * 60)
    click.echo(f"Timeline entries: {result['entries']}")
    click.echo(f"People: {result['people']}")
    click.echo(f"Dynasties: {result['dynasties']}")
    click.echo(f"Groups: {result['groups']}")
    click.echo("=" * 60)


if __name__ == "__main__":
    main()
