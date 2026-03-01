# Report Format Reference

Each run of `cloud_duplicate_analyzer.py` produces two output files with the same base name:

- `<output>.html` — Visual report, open in any browser
- `<output>.json` — Raw structured data

## HTML Report

The HTML report has five sections.

### Section 1: File Counts

A stat grid showing the number of files in each directory and its percentage of the total, followed by a table listing the full path of each directory.

### Section 2: Duplicate File Summary

A table showing pairwise duplicate counts (e.g. how many files appear in both Google Drive and Dropbox) and, when three or more directories are compared, the count of files present in all services simultaneously.

Also shows the number of files that are **unique** to each service (i.e. not duplicated anywhere).

### Section 3: Folder Structure Analysis

Two parts:

- **Part 1 — Fully duplicated subtrees** panel: a table listing each `safe_to_delete_roots` entry with a per-service ✓ or — column and a total file count for the subtree. Only shown when at least one fully-identical subtree exists.

- **Part 2 — Folder tree**: collapsible `<details>`/`<summary>` nodes. Each node shows the subtree status symbol (★ = identical subtree, ~ = partially duplicated, ✗ = has conflicts), per-folder file counts, and file-level detail within each expanded folder. Files shared across services are listed under "Shared across services" and annotated with ★/✓/⚠/⚡ per their match status; ⚠ and ⚡ files link to Section 4. Files unique to one service are listed under "Only in &lt;service&gt;" with a → marker.

### Section 4: Files Requiring Action

Files that share a name and size across services but have **different content** (i.e. `content_match = "different"`). Sorted by age gap (largest first). Columns:

| Column | Description |
|---|---|
| File | Filename |
| Folder | Relative folder path |
| Status | `different · diverged` (timestamps differ) or `different · phantom` (timestamps agree but content differs) |
| Per-service columns | Size and modification timestamp for each service |

### Section 5: Duplicate Files

A row per confirmed duplicate group (files with `content_match = identical` or `unverified`). Columns:

| Column | Description |
|---|---|
| File | Filename |
| Folder | Relative folder path within the directory |
| Size | Human-readable file size |
| Found in | Which services contain this file |
| Match | Combined `content_match · version_status` badge, e.g. `identical · same`, `identical · diverged`, `unverified · same` |

---

## JSON Output Schema

```json
{
  "labels": ["Google Drive", "Dropbox", "OneDrive"],
  "dirs": {
    "Google Drive": "/Users/me/Google Drive",
    "Dropbox": "/Users/me/Dropbox",
    "OneDrive": "/Users/me/OneDrive"
  },
  "total_files": {
    "Google Drive": 656,
    "Dropbox": 1506,
    "OneDrive": 6023
  },
  "duplicate_groups": [
    {
      "rel_path": "Documents/Reading/Security/NIST.SP.800-207.pdf",
      "name_orig": "NIST.SP.800-207.pdf",
      "size": 990208,
      "matches": {
        "Google Drive": { "rel_path": "...", "name": "...", "size": 990208, "mtime": 1234567890.0 },
        "Dropbox":      { "..." : "..." },
        "OneDrive":     { "..." : "..." }
      },
      "content_match": "identical",
      "version_status": "same",
      "newest_in": null,
      "age_difference_days": 0.0
    }
  ],
  "conflict_groups": [
    {
      "rel_path": "Documents/budget.xlsx",
      "name_orig": "budget.xlsx",
      "size": 24576,
      "content_match": "different",
      "version_status": "diverged",
      "newest_in": "Dropbox",
      "age_difference_days": 3.2,
      "service_details": {
        "Google Drive": { "size": 24576, "mtime": "2009-02-13 23:30 UTC", "mtime_raw": 1234567800.0 },
        "Dropbox":      { "size": 24576, "mtime": "2009-02-16 07:43 UTC", "mtime_raw": 1234844600.0 }
      }
    }
  ],
  "unique_counts": {
    "Google Drive": 137,
    "Dropbox": 904,
    "OneDrive": 5638
  },
  "pairwise_counts": {
    "Google Drive↔Dropbox": 512,
    "Google Drive↔OneDrive": 512,
    "Dropbox↔OneDrive": 595
  },
  "all_services_count": 504,
  "folder_comparisons": [
    {
      "folder_path": "Documents/BoA",
      "services_present": ["Google Drive", "Dropbox", "OneDrive"],
      "relationship": "identical",
      "total_unique_files": 5,
      "files_in_all": 5,
      "details": {
        "in_all": ["file1.pdf", "file2.pdf"],
        "Google Drive_only": [],
        "Dropbox_only": [],
        "OneDrive_only": []
      }
    }
  ],
  "safe_to_delete_roots": [
    {
      "folder_path": "Photos/2020",
      "subtree_status": "identical",
      "subtree_total_files": 42
    }
  ],
  "relationship_counts": {
    "identical": 48,
    "overlap": 6,
    "subset/superset": 8
  },
  "generated_at": "2026-02-28 14:30",
  "mtime_fuzz": 5
}
```

### `duplicate_groups[].content_match` and `duplicate_groups[].version_status`

The `confidence` field from earlier versions has been replaced by two independent fields:

**`content_match`**

| Value | Meaning |
|---|---|
| `identical` | MD5 checksums confirmed the file content matches across services |
| `unverified` | `--no-checksum` was used; name + size agree but content was not verified |

**`version_status`**

| Value | Meaning |
|---|---|
| `same` | All copies have mtimes within the fuzz window |
| `diverged` | At least one copy has a mtime more than `mtime_fuzz` seconds away from another |

### `conflict_groups`

Array of file groups where `content_match = "different"` — files that share a name and size but have differing MD5 checksums. These are separated from `duplicate_groups` because they require manual review before any deletion.

Each entry mirrors the shape of `duplicate_groups` entries but includes `service_details` with per-service `size`, `mtime` (formatted string, e.g. `"2024-01-15 10:00 UTC"`), and `mtime_raw` (Unix timestamp float) fields, and always has `content_match = "different"`.

| `version_status` value | Meaning |
|---|---|
| `diverged` | Content differs and timestamps also differ — keep the newer copy |
| `phantom` | Content differs despite matching timestamps — keep both copies |

### `safe_to_delete_roots`

Array of folder paths whose entire subtree (all descendant folders) is classified `identical` across all compared services. These are the highest-level folders safe to delete — subfolders are omitted since they are already covered by their ancestor.
