# How It Works

This document describes the matching algorithm and folder analysis logic used by `cloud_duplicate_analyzer.py`.

## File Scanning

Each directory is walked recursively using `os.walk`. For every file the script records:

- **Relative path** from the directory root (used to map the same logical location across services)
- **Filename** (lowercased for case-insensitive comparison)
- **Size** in bytes
- **Modification time** (`mtime`) as a Unix timestamp

Hidden files and folders (names starting with `.`) are skipped by default. `.DS_Store` files are always skipped.

## Duplicate Matching

Files are matched in two stages.

### Stage 1 — Name + Size Index

An index is built for each directory keyed on `(lowercase_name, size_in_bytes)`. This lets the script find candidate pairs in O(1) without comparing every file against every other file.

Any key that appears in two or more directories is a candidate duplicate group.

### Stage 2 — Confidence Scoring

Candidates are confirmed with the following rules, applied in order:

| Condition | Result |
|---|---|
| Same name + same size + `mtime` within fuzz window | **exact** match |
| Same name + same size + `mtime` differs + MD5 checksums match | **likely** match (same content, sync tool changed the timestamp) |
| Same name + same size + `mtime` differs + checksums skipped | **likely** match (assumed — use `--no-checksum` consciously) |
| Same name + size is 0 | **exact** match (empty files) |
| Different size | No match |

The fuzz window defaults to 5 seconds and can be changed with `--mtime-fuzz`. Some sync tools (notably OneDrive) round timestamps to the nearest second, which can cause sub-second differences between otherwise identical files.

MD5 checksums are only computed when name and size match but modification times differ — so the common case (many identical files with identical mtimes) does not touch disk content at all.

## Version Divergence

After a duplicate group is confirmed, the modification times of all copies are compared. If the spread between the oldest and newest copy exceeds the fuzz window, the group is marked **diverged** and the service holding the newest copy is recorded.

This surfaces files that were edited in one location and not synced to others — the most important information when deciding which copy to keep before deleting duplicates.

## Folder Analysis

For every folder path that appears in two or more directories, the set of filenames in that folder is compared across services. The relationship is classified as:

| Relationship | Meaning |
|---|---|
| **identical** | All services have exactly the same set of files in this folder |
| **subset/superset** | Every file in service A also exists in service B, but B has additional files |
| **overlap** | Each service has some files the others don't; neither is a subset of the other |

Note: folder analysis is based on filenames only (not file content), so two folders can be classified as "identical" if their file names and counts match even if the content has diverged. Cross-reference with Section 3 (duplicate file list) for content-confirmed matches.

## Performance Notes

- For large directories (tens of thousands of files), the bottleneck is MD5 computation on large files. Use `--no-checksum` to skip this if speed matters more than precision.
- The name+size index means the matching step itself is fast regardless of directory size.
- Memory usage is proportional to the total number of files (one record per file).
