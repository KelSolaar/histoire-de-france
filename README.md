# Histoire de France

Interactive timeline of French history based on Jacques Bainville's
*Histoire de France* (1924). 883 events spanning from 2500 BCE to 1923,
each with source excerpts, Wikipedia images, and thematic tags.

**[Live demo](https://kelsolaar.github.io/histoire-de-france/)**

## Features

- Canvas-rendered timeline grouped by dynasty or chapter
- Keyboard navigation (arrow keys) with auto-zoom to resolve overlapping events
- Importance filtering (Fondateur through Anecdotique)
- Tag filtering across 22 categories (Politique, Guerre, Bataille, Religion, ...)
- Full-text search
- Source reader with highlighted excerpts from the original text
- Wikipedia images with attribution (872/883 entries)
- Service worker caching for images

## Quick Start

```sh
npm install
npm run dev
```

Open http://localhost:5173.

## Build & Deploy

```sh
npm run build
```

Static output goes to `dist/`. The repository includes a GitHub Actions
workflow (`.github/workflows/deploy.yml`) that builds and deploys to
GitHub Pages on every push to `main`.

## Data Pipeline

The timeline data is generated from the source text through a multi-step
pipeline. Each step is a standalone Python script run with
[uv](https://docs.astral.sh/uv/):

```sh
uv run scripts/pipeline.py                       # run all steps
uv run scripts/pipeline.py --from verify          # resume from a step
uv run scripts/pipeline.py --steps extract,merge   # run specific steps
```

| Step      | Script                 | Description                                          |
|-----------|------------------------|------------------------------------------------------|
| `chunk`   | `text_chunker.py`      | Split source text into 22 chapter files              |
| `extract` | `history_extractor.py` | Extract events from each chapter using an LLM        |
| `merge`   | `data_merger.py`       | Merge per-chapter JSON into `timeline_entries.json`  |
| `verify`  | `verify_sources.py`    | Verify excerpts against source text, fix line ranges |
| `tag`     | `tag_entries.py`       | Assign thematic tags using an LLM                    |
| `images`  | `fetch_images.py`      | Fetch Wikipedia images and attribution               |

LLM steps (`extract`, `tag`) support `--tool gemini` or `--tool claude`.

## Project Structure

```
src/
  App.tsx              Main application, filtering, keyboard navigation
  CanvasTimeline.tsx   Canvas-rendered timeline with overlap culling
  types.ts             TypeScript type definitions
data/
  timeline_entries.json  883 events with dates, excerpts, tags, images
  chapters/              22 chapter text files from the source book
scripts/
  pipeline.py          Orchestrates the full data pipeline
  text_chunker.py      Splits source text by chapter headings
  history_extractor.py LLM-based event extraction
  data_merger.py       Merges extracted chapter data
  verify_sources.py    Verifies and fixes source references
  tag_entries.py       LLM-based tag assignment
  fetch_images.py      Wikipedia image fetcher
docs/
  Histoire-de-France-Jacques-Bainville.txt  Source text
```

## Tech Stack

- React 18, TypeScript, Tailwind CSS 4, Vite 6
- Canvas 2D for timeline rendering
- Python scripts (uv) for data processing

## License

Code is released under the [BSD-3-Clause](LICENSE) license. The source text
(*Histoire de France*, Jacques Bainville, 1924) is in the public domain.
Images are sourced from Wikimedia Commons under their respective licenses.
