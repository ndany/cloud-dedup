# cloud-dedup

Scans two or more directories and produces an HTML report identifying duplicate files across them — designed for comparing cloud storage sync folders (Google Drive, Dropbox, OneDrive, etc.).

## Features

- **File-level duplicate detection** using filename + size, modification time, and optional MD5 checksum verification
- **Version divergence detection** — flags files that exist in multiple places but have different modification dates, so you know which copy is newest before deleting
- **Folder-level structure analysis** — classifies each shared folder as identical, a subset/superset, or partially overlapping
- **Self-contained** — pure Python 3.8+, no third-party packages required
- **Two outputs per run** — an HTML report (opens in any browser) and a JSON file for programmatic use

## Quick Start

```bash
python3 src/cloud_duplicate_analyzer.py \
    "Google Drive:~/Google Drive" \
    "Dropbox:~/Dropbox" \
    "OneDrive:~/OneDrive"
```

The HTML report is written to `cloud_duplicate_report.html` in the current directory by default.

## Usage

```
python3 src/cloud_duplicate_analyzer.py <dir1> <dir2> [dir3 ...] [options]
```

### Positional Arguments

| Argument | Description |
|---|---|
| `dir1`, `dir2`, ... | Paths to compare. Prefix with `Label:` to set a display name, e.g. `"GDrive:~/Google Drive"`. If no label is given, the last path component is used. |

### Options

| Flag | Default | Description |
|---|---|---|
| `-o`, `--output FILE` | auto-named in current dir | Full output file path. The `.html` and `.json` files share this stem. When omitted, a timestamped name is used (see below). |
| `--output-dir DIR` | — | Directory for the auto-named output files. The filename is generated as `cloud_duplicate_report_YYMMDDHHMM.html`. Ignored when `-o` is given. |
| `--mtime-fuzz N` | `5` | Seconds of tolerance when comparing modification times |
| `--no-checksum` | off | Skip MD5 checksums; rely on name + size + mtime only (faster) |
| `--include-hidden` | off | Include hidden files/folders (names starting with `.`) |

### Output naming

Unless you pass `-o` with an explicit path, the output filename is always timestamped:

```
cloud_duplicate_report_YYMMDDHHMM.html   (e.g. cloud_duplicate_report_2602281430.html)
cloud_duplicate_report_YYMMDDHHMM.json
```

This means repeated runs never overwrite each other. Use `--output-dir` to point all runs at a fixed folder without having to specify the filename each time.

### Examples

```bash
# Minimal — timestamped report written to current directory
python3 src/cloud_duplicate_analyzer.py ~/Google\ Drive ~/Dropbox ~/OneDrive

# Fixed output folder — filename auto-generated with timestamp
python3 src/cloud_duplicate_analyzer.py \
    "GDrive:~/Google Drive" \
    "Dropbox:~/Dropbox" \
    "OneDrive:~/OneDrive" \
    --output-dir ~/OneDrive/Reports

# Explicit output path (you control the filename)
python3 src/cloud_duplicate_analyzer.py ~/Google\ Drive ~/Dropbox \
    -o ~/Desktop/dup_report.html

# Two-way comparison after partial cleanup
python3 src/cloud_duplicate_analyzer.py ~/Dropbox ~/OneDrive

# Faster run — no MD5 checksums
python3 src/cloud_duplicate_analyzer.py ~/Google\ Drive ~/Dropbox --no-checksum

# Looser timestamp tolerance (useful if sync tools shift mtimes)
python3 src/cloud_duplicate_analyzer.py ~/Google\ Drive ~/OneDrive --mtime-fuzz 60
```

## Output

Each run produces two files side-by-side:

| File | Description |
|---|---|
| `<output>.html` | Full visual report — open in any browser |
| `<output>.json` | Raw analysis data for scripting or further processing |

The HTML report has five sections:

1. **File Counts** — how many files are in each directory
2. **Duplicate File Summary** — pairwise and all-services duplicate counts
3. **Duplicate Files** — every confirmed duplicate with size, match confidence, and version status
4. **Version-Diverged Files** — files whose copies have different modification dates, with the newest copy called out
5. **Folder Structure Analysis** — each shared folder classified as identical, subset/superset, or overlapping, with per-service unique file lists

Rows highlighted in **yellow** need attention: they are duplicates where the copies have drifted out of sync.

## Project Structure

```
cloud-dedup/
├── src/
│   └── cloud_duplicate_analyzer.py   # Main script
├── docs/
│   ├── how-it-works.md               # Matching algorithm detail
│   └── report-format.md              # HTML/JSON output reference
├── output/                           # Generated reports (git-ignored)
├── .gitignore
└── README.md
```

## Requirements

- Python 3.8 or later
- No third-party packages

## License

MIT
