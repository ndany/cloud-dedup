# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the Tool

```bash
# Basic usage (requires 2+ directories)
python3 src/cloud_duplicate_analyzer.py "Label1:/path/to/dir1" "Label2:/path/to/dir2"

# Custom output path (default is cloud_duplicate_report_YYMMDDHHMM.html in current dir)
python3 src/cloud_duplicate_analyzer.py ~/Google\ Drive ~/Dropbox -o output/report.html

# Auto-named timestamped file in a fixed directory (--output-dir ignored when -o given)
python3 src/cloud_duplicate_analyzer.py ~/Google\ Drive ~/Dropbox --output-dir output/

# Skip MD5 checksums for speed
python3 src/cloud_duplicate_analyzer.py ~/Google\ Drive ~/Dropbox --no-checksum

# Loosen timestamp tolerance (default 5s; useful for OneDrive which rounds mtimes)
python3 src/cloud_duplicate_analyzer.py ~/Google\ Drive ~/OneDrive --mtime-fuzz 60
```

Outputs are always a pair: `<name>.html` and `<name>.json` written side-by-side. Both are gitignored via `output/` and `*.html`/`*.json` patterns. When neither `-o` nor `--output-dir` is given, the file is written to the current directory with a timestamp suffix (`cloud_duplicate_report_YYMMDDHHMM.html`).

## Architecture

The entire tool lives in a single file: `src/cloud_duplicate_analyzer.py`. No third-party packages; stdlib only. Python 3.8+.

**Pipeline** (all orchestrated by `analyze()`):

1. **Scan** (`scan_directory`) — `os.walk` each directory, collecting `rel_path`, `name` (lowercased), `size`, `mtime`, `full_path`, `folder`.

2. **Index** (`build_name_size_index`) — builds a `(lowercase_name, size_bytes) → [records]` dict per directory for O(1) candidate lookup.

3. **Match** (`files_match`) — two-stage confidence scoring:
   - `"exact"`: same name + size + mtime within fuzz window (or size==0)
   - `"likely"`: same name + size, mtime differs, but MD5 matches (or `--no-checksum` assumed)
   - `""`: no match
   MD5 is only computed when name+size match but mtime differs — avoids disk reads in the common case.

4. **Version divergence** — after confirming a duplicate group, if the mtime spread across copies exceeds the fuzz window, the group is marked `"diverged"` with `newest_in` pointing to the service with the latest copy.

5. **Folder analysis** — for every folder path shared by 2+ directories, compares the filename sets and classifies as `"identical"`, `"subset/superset"`, or `"overlap"`. Note: folder analysis is filename-only, not content-verified.

6. **Render** (`render_html`) — inline CSS + HTML string built with `html.escape()`. No template engine.

**Key data structures:**
- `duplicate_groups`: list of dicts with `rel_path`, `name_orig`, `size`, `matches` (label→record), `confidence`, `version_status`, `newest_in`, `age_difference_days`, `copy_mtimes`
- `folder_comparisons`: list of dicts with `folder_path`, `services_present`, `relationship`, `details` (per-label `_only` sets and `in_all`)
- The final `result` dict is serialised directly to JSON (with `Path` objects coerced via `default=str`)

## Docs

- `docs/how-it-works.md` — matching algorithm and folder analysis logic in detail
- `docs/report-format.md` — HTML section descriptions and full JSON schema with example
