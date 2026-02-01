# /// script
# dependencies = [
#   "click",
# ]
# requires-python = ">=3.10"
# ///

"""
Processing Pipeline

Single CLI entry point that runs the full data processing pipeline,
from text chunking through extraction, merging, verification, tagging,
and image fetching.

Usage:
    uv run scripts/pipeline.py
    uv run scripts/pipeline.py --steps extract,merge,verify
    uv run scripts/pipeline.py --from verify
    uv run scripts/pipeline.py --tool gemini --verbose
"""

import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import click

SCRIPTS_DIR = Path(__file__).parent


# ==============================================================================
# PIPELINE STEPS
# ==============================================================================


@dataclass
class Step:
    """A pipeline step definition."""

    name: str
    script: str
    description: str
    needs_tool: bool = False

    def build_command(
        self,
        tool: str | None = None,
        verbose: bool = False,
    ) -> list[str]:
        """Build the uv run command for this step."""
        cmd = ["uv", "run", str(SCRIPTS_DIR / self.script)]

        if self.name == "chunk":
            cmd += [
                "-i",
                "docs/Histoire-de-France-Jacques-Bainville.txt",
                "-o",
                "data/chapters/",
                "-p",
                "^CHAPITRE",
                "--prefix",
                "chapter",
            ]
        elif self.name == "extract":
            cmd += [
                "-i",
                "data/chapters/",
                "-o",
                "data/extracted/",
            ]
            if tool:
                cmd += ["-t", tool]
        elif self.name == "clean":
            cmd += [
                "-i",
                "data/extracted/",
            ]
        elif self.name == "merge":
            cmd += [
                "-i",
                "data/extracted/",
                "-o",
                "data/",
            ]
        elif self.name == "verify":
            cmd += [
                "-i",
                "data/timeline_entries.json",
                "-c",
                "data/chapters/",
            ]
        elif self.name == "tag":
            cmd += [
                "-i",
                "data/timeline_entries.json",
            ]
            if tool:
                cmd += ["--tool", tool]
        elif self.name == "images":
            cmd += [
                "--input",
                "data/timeline_entries.json",
            ]

        # history_extractor.py does not support --verbose
        if verbose and self.name not in ("extract",):
            cmd.append("-v")

        return cmd


STEPS = [
    Step("chunk", "text_chunker.py", "Split source text into chapters"),
    Step("extract", "history_extractor.py", "Extract events with LLM", needs_tool=True),
    Step("clean", "clean_extracted.py", "Clean LLM artifacts from extracted data"),
    Step("merge", "data_merger.py", "Merge extracted data"),
    Step("verify", "verify_sources.py", "Verify excerpts & fix line ranges"),
    Step("tag", "tag_entries.py", "Assign tags with LLM", needs_tool=True),
    Step("images", "fetch_images.py", "Fetch Wikipedia images"),
]

STEP_NAMES = [s.name for s in STEPS]


# ==============================================================================
# MAIN
# ==============================================================================


def resolve_steps(
    steps: str | None, from_step: str | None
) -> list[Step]:
    """Resolve which steps to run based on --steps and --from options."""
    if steps and from_step:
        raise click.UsageError("Cannot use both --steps and --from.")

    if steps:
        names = [s.strip() for s in steps.split(",")]
        for name in names:
            if name not in STEP_NAMES:
                raise click.UsageError(
                    f"Unknown step '{name}'. Available: {', '.join(STEP_NAMES)}"
                )
        return [s for s in STEPS if s.name in names]

    if from_step:
        if from_step not in STEP_NAMES:
            raise click.UsageError(
                f"Unknown step '{from_step}'. Available: {', '.join(STEP_NAMES)}"
            )
        idx = STEP_NAMES.index(from_step)
        return STEPS[idx:]

    return list(STEPS)


@click.command()
@click.option(
    "--steps",
    "-s",
    type=str,
    default=None,
    help="Comma-separated list of steps to run (e.g. extract,merge,verify).",
)
@click.option(
    "--from",
    "-f",
    "from_step",
    type=str,
    default=None,
    help="Run from this step onward (e.g. verify).",
)
@click.option(
    "--tool",
    "-t",
    type=click.Choice(["gemini", "claude"]),
    default="gemini",
    help="LLM tool for extraction and tagging (default: gemini).",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    default=False,
    help="Enable verbose output.",
)
def main(steps: str | None, from_step: str | None, tool: str, verbose: bool):
    """
    Run the full data processing pipeline (or selected steps).

    Steps (in order): chunk, extract, clean, merge, verify, tag, images.

    Examples:

        uv run scripts/pipeline.py

        uv run scripts/pipeline.py --from verify

        uv run scripts/pipeline.py --steps extract,merge,verify --tool gemini
    """
    selected = resolve_steps(steps, from_step)

    click.echo("=" * 60)
    click.echo("PIPELINE")
    click.echo("=" * 60)
    click.echo(f"Steps: {', '.join(s.name for s in selected)}")
    click.echo(f"Tool:  {tool}")
    click.echo("=" * 60)

    total_start = time.time()

    for i, step in enumerate(selected, 1):
        cmd = step.build_command(tool=tool, verbose=verbose)

        click.echo(f"\n[{i}/{len(selected)}] {step.name}: {step.description}")
        click.echo(f"  $ {' '.join(cmd)}")

        step_start = time.time()
        result = subprocess.run(cmd)
        elapsed = time.time() - step_start

        if result.returncode != 0:
            click.echo(f"\n  FAILED (exit code {result.returncode}) after {elapsed:.1f}s")
            sys.exit(result.returncode)

        click.echo(f"  done ({elapsed:.1f}s)")

    total_elapsed = time.time() - total_start

    click.echo("\n" + "=" * 60)
    click.echo(f"PIPELINE COMPLETE ({total_elapsed:.1f}s)")
    click.echo("=" * 60)


if __name__ == "__main__":
    main()
