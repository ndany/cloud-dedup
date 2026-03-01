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

### Stage 2 — Content and Version Classification

Every candidate pair is classified on two independent dimensions:

| content_match | version_status | Meaning | Action |
|---|---|---|---|
| `identical` | `same` | MD5 match + mtime within fuzz | safe to delete either copy |
| `identical` | `diverged` | MD5 match + mtime differs | safe (sync timestamp artifact) |
| `different` | `diverged` | MD5 mismatch + mtime differs | keep newer copy |
| `different` | `phantom` | MD5 mismatch + mtime within fuzz | keep both — dangerous |
| `unverified` | `same` | `--no-checksum`, mtime within fuzz | assumed match |
| `unverified` | `diverged` | `--no-checksum`, mtime differs | assumed match, may be stale |

MD5 checksums are computed for **all** name+size candidates. Use `--no-checksum` to skip checksums for speed; matches will be labelled `unverified` and the `phantom` case cannot be detected.

Files with `content_match = identical` or `unverified` go into the **duplicate groups** (Section 5).
Files with `content_match = different` go into **conflict groups** (Section 4 — Files Requiring Action).

Empty files (size == 0) are always classified `(identical, same)` regardless of mtime.

## Symlink Handling

### Detection Scope

Only **file-type symlinks** are detected and reported. Directory symlinks appear in `dirnames` during `os.walk`. With `followlinks=False` (the default), `os.walk` does not descend into them — files inside a directory symlink are not scanned. The symlink directory itself is also not reported as a symlink entry. The tool's symlink detection covers file-type symlinks only.

### Detection Method

Before calling `Path.stat()`, `Path.is_symlink()` is checked. A symlink record carries:

- `is_symlink = True`
- `symlink_target` — the resolved absolute path as a string, or `None` for a dangling (broken) symlink
- `size = -1` — a sentinel value indicating no meaningful file size

On macOS, `Path.resolve()` returns a non-`None` path even for dangling symlinks. On other platforms, resolution of a broken symlink may raise an `OSError`, in which case `symlink_target` is recorded as `None`.

### Comparison

Symlinks are compared by their **resolved target path string**, not by reading the target's content. Two symlinks in different services are considered "identical" if their `symlink_target` strings are equal.

### Results Routing

| Situation | Destination |
|---|---|
| Both services have a symlink at the same relative path | `symlinks` list (informational) |
| One service has a regular file, another has a symlink at the same name | `conflict_groups` with `content_match = "mixed_type"` (Section 4) |
| Both services have symlinks but targets differ | `symlinks` list with `symlink_status = "target_diverged"` (surfaced in Section 4 at render time) |

Diverged symlinks (`target_diverged`) appear in **Section 4 — Files Requiring Action** because the services disagree on where the symlink points.

## Folder Analysis

For every folder path that appears in two or more directories, the set of filenames in that folder is compared across services. The relationship is classified as:

| Relationship | Meaning |
|---|---|
| **identical** | All services have exactly the same set of files in this folder |
| **subset/superset** | Every file in service A also exists in service B, but B has additional files |
| **overlap** | Each service has some files the others don't; neither is a subset of the other |

Note: folder analysis is based on filenames only (not file content), so two folders can be classified as "identical" if their file names and counts match even if the content has diverged. Cross-reference with Section 3 (duplicate file list) for content-confirmed matches.

## Subtree Rollups

After leaf-level folder comparison, each folder is assigned a `subtree_status` based on all its descendant folders:

- **identical** — every folder in the subtree has identical file sets across all services
- **partial** — some folders match, others don't
- **overlap** — at least one folder has files unique to each service

Folders with `subtree_status = identical` are candidates for safe deletion. The report surfaces only the **highest-level** identical roots — deleting `Photos/` covers all subfolders, so `Photos/2020/` is not listed separately.

## Performance Notes

- For large directories (tens of thousands of files), the bottleneck is MD5 computation on large files. Use `--no-checksum` to skip this if speed matters more than precision.
  - **Default**: Computes MD5 for all name+size candidates, providing confidence in matches and detecting "phantom" false positives (content differs despite identical timestamps).
  - **With `--no-checksum`**: Relies on filename + size + modification time only. Matches are labeled `unverified` and the phantom case cannot be detected. Trade speed for reduced confidence.
- The name+size index means the matching step itself is fast regardless of directory size.
- Memory usage is proportional to the total number of files (one record per file).
