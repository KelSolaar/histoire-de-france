# /// script
# dependencies = [
#   "click",
# ]
# requires-python = ">=3.10"
# ///

"""
Reusable Text Chunking Script

A flexible, single-file Python script that splits text files into chunks
based on a regex pattern. Preserves line numbers for source traceability.

Usage:
    uv run scripts/text_chunker.py --pattern "^CHAPITRE" \\
        --input docs/input.txt --output data/chapters/
"""

import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import click

# ==============================================================================
# CONFIGURATION
# ==============================================================================


@dataclass
class ChunkingConfig:
    """Configuration settings for text chunking."""

    # Output settings
    output_prefix: str = "chunk"
    output_extension: str = ".txt"
    padding_digits: int = 2

    # Content settings
    include_header: bool = True
    header_format: str = "# Lines {start}-{end} from {source}\n\n"


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


# ==============================================================================
# DATA STRUCTURES
# ==============================================================================


@dataclass
class Chunk:
    """Container for a text chunk with metadata."""

    index: int
    title: str
    content: str
    line_start: int
    line_end: int
    source_file: str
    metadata: dict = field(default_factory=dict)

    @property
    def line_count(self) -> int:
        """Return the number of lines in this chunk."""
        return self.line_end - self.line_start + 1


@dataclass
class ChunkingResult:
    """Result of chunking a text file."""

    source_file: str
    total_lines: int
    chunks: list[Chunk] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def chunk_count(self) -> int:
        """Return the number of chunks."""
        return len(self.chunks)


# ==============================================================================
# CORE CLASSES
# ==============================================================================


class TextChunker:
    """Splits text files into chunks based on regex patterns."""

    def __init__(self, config: Optional[ChunkingConfig] = None):
        self.config = config or ChunkingConfig()
        self.logger = logging.getLogger(
            f"{__name__}.{self.__class__.__name__}"
        )

    def chunk_file(
        self,
        file_path: Path,
        pattern: str,
        title_pattern: Optional[str] = None,
    ) -> ChunkingResult:
        """
        Split a text file into chunks based on a regex pattern.

        Parameters
        ----------
        file_path
            Path to the input text file.
        pattern
            Regex pattern that marks the start of each chunk.
        title_pattern
            Optional regex to extract chunk title from the matching line.
            If None, uses the full matching line as title.

        Returns
        -------
        ChunkingResult
            Result containing all chunks and metadata.
        """
        self.logger.info("Reading file: %s", file_path)

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except (OSError, UnicodeDecodeError) as error:
            self.logger.error("Failed to read file %s: %s", file_path, error)
            return ChunkingResult(
                source_file=str(file_path),
                total_lines=0,
                errors=[str(error)],
            )

        total_lines = len(lines)
        self.logger.info("File has %s lines", total_lines)

        # Compile the pattern
        try:
            regex = re.compile(pattern)
        except re.error as error:
            self.logger.error("Invalid regex pattern '%s': %s", pattern, error)
            return ChunkingResult(
                source_file=str(file_path),
                total_lines=total_lines,
                errors=[f"Invalid regex: {error}"],
            )

        # Find all chunk boundaries
        boundaries: list[tuple[int, str]] = []
        for i, line in enumerate(lines, start=1):
            if regex.match(line):
                # Extract title
                if title_pattern:
                    title_match = re.search(title_pattern, line)
                    title = title_match.group(1) if title_match else line.strip()
                else:
                    title = line.strip()
                boundaries.append((i, title))

        self.logger.info("Found %s chunk boundaries", len(boundaries))

        if not boundaries:
            self.logger.warning("No chunks found with pattern '%s'", pattern)
            return ChunkingResult(
                source_file=str(file_path),
                total_lines=total_lines,
                errors=["No chunks found"],
            )

        # Create chunks
        chunks: list[Chunk] = []
        for idx, (start_line, title) in enumerate(boundaries):
            # Determine end line (next boundary - 1, or end of file)
            if idx + 1 < len(boundaries):
                end_line = boundaries[idx + 1][0] - 1
            else:
                end_line = total_lines

            # Extract content (convert to 0-indexed)
            chunk_lines = lines[start_line - 1 : end_line]
            content = "".join(chunk_lines)

            chunk = Chunk(
                index=idx + 1,
                title=title,
                content=content,
                line_start=start_line,
                line_end=end_line,
                source_file=str(file_path),
            )
            chunks.append(chunk)

            self.logger.debug(
                "Chunk %s: '%s' (lines %s-%s, %s lines)",
                idx + 1,
                title[:50],
                start_line,
                end_line,
                chunk.line_count,
            )

        return ChunkingResult(
            source_file=str(file_path),
            total_lines=total_lines,
            chunks=chunks,
        )

    def write_chunks(
        self,
        result: ChunkingResult,
        output_dir: Path,
        include_header: bool = True,
    ) -> list[Path]:
        """
        Write chunks to individual files.

        Parameters
        ----------
        result
            ChunkingResult containing chunks to write.
        output_dir
            Directory to write chunk files to.
        include_header
            Whether to include metadata header in each file.

        Returns
        -------
        list[Path]
            Paths to the written files.
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        written_files: list[Path] = []

        for chunk in result.chunks:
            # Generate filename
            filename = (
                f"{self.config.output_prefix}"
                f"_{str(chunk.index).zfill(self.config.padding_digits)}"
                f"{self.config.output_extension}"
            )
            output_path = output_dir / filename

            # Build content
            if include_header:
                header = self.config.header_format.format(
                    start=chunk.line_start,
                    end=chunk.line_end,
                    source=chunk.source_file,
                    title=chunk.title,
                    index=chunk.index,
                )
                content = header + chunk.content
            else:
                content = chunk.content

            # Write file
            try:
                with open(output_path, "w", encoding="utf-8") as f:
                    f.write(content)
                written_files.append(output_path)
                self.logger.info("Wrote: %s", output_path)
            except OSError as error:
                self.logger.error(
                    "Failed to write %s: %s", output_path, error
                )

        return written_files


# ==============================================================================
# MAIN
# ==============================================================================


@click.command()
@click.option(
    "--input",
    "-i",
    "input_path",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help="Input text file to chunk.",
)
@click.option(
    "--output",
    "-o",
    "output_dir",
    type=click.Path(path_type=Path),
    required=True,
    help="Output directory for chunk files.",
)
@click.option(
    "--pattern",
    "-p",
    type=str,
    required=True,
    help="Regex pattern marking chunk boundaries (e.g., '^CHAPITRE').",
)
@click.option(
    "--title-pattern",
    "-t",
    type=str,
    default=None,
    help="Regex to extract title from boundary line (group 1 used).",
)
@click.option(
    "--prefix",
    type=str,
    default="chunk",
    help="Filename prefix for output files (default: 'chunk').",
)
@click.option(
    "--no-header",
    is_flag=True,
    default=False,
    help="Don't include metadata header in output files.",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    default=False,
    help="Enable verbose (debug) output.",
)
def main(
    input_path: Path,
    output_dir: Path,
    pattern: str,
    title_pattern: Optional[str],
    prefix: str,
    no_header: bool,
    verbose: bool,
):
    """
    Split a text file into chunks based on a regex pattern.

    Examples:

        # Split by chapter markers
        uv run text_chunker.py -i book.txt -o chapters/ -p "^CHAPITRE"

        # Split with custom prefix
        uv run text_chunker.py -i doc.txt -o parts/ -p "^## " --prefix part

        # Extract title from pattern
        uv run text_chunker.py -i book.txt -o ch/ -p "^CHAPITRE" \\
            -t "CHAPITRE (.+)"
    """
    setup_logging(logging.DEBUG if verbose else logging.INFO)

    config = ChunkingConfig(output_prefix=prefix)
    chunker = TextChunker(config)

    LOGGER.info("Chunking %s with pattern '%s'", input_path, pattern)

    result = chunker.chunk_file(input_path, pattern, title_pattern)

    if result.errors:
        for error in result.errors:
            LOGGER.error("Error: %s", error)
        sys.exit(1)

    LOGGER.info(
        "Found %s chunks in %s lines",
        result.chunk_count,
        result.total_lines,
    )

    written = chunker.write_chunks(result, output_dir, include_header=not no_header)

    LOGGER.info("Wrote %s files to %s", len(written), output_dir)

    # Print summary
    click.echo("\n" + "=" * 60)
    click.echo("CHUNKING SUMMARY")
    click.echo("=" * 60)
    click.echo(f"Source: {input_path}")
    click.echo(f"Total lines: {result.total_lines}")
    click.echo(f"Chunks found: {result.chunk_count}")
    click.echo("")
    for chunk in result.chunks:
        click.echo(
            f"  {chunk.index}. {chunk.title[:40]} (lines {chunk.line_start}-{chunk.line_end})"
        )
    click.echo("=" * 60)


if __name__ == "__main__":
    main()
