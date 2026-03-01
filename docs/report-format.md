# Report Format Reference

Each run of `cloud_duplicate_analyzer.py` produces two output files with the same base name:

- `<output>.html` — Visual report, open in any browser
- `<output>.json` — Raw structured data

## Color Scheme

All badge pills and status indicators in the report follow a consistent semantic color scheme:

| Color | Values | Meaning |
|---|---|---|
| **Green** | `identical`, `same` | Safe to delete — content confirmed identical and timestamps agree |
| **Amber** | `diverged`, `unverified`, `symlink` | Requires review — content may match but needs verification or a decision |
| **Red** | `different`, `phantom`, `conflict` / mixed-type | Not safe to delete — content differs or conflict cannot be resolved automatically |

Row highlighting uses the same scheme with lighter background tints:
- Red/phantom/conflict rows → light red background
- Amber/diverged rows → light amber background

This applies consistently across all sections: Section 2 badge pills, Section 5 Match and Version badges, and any future status indicators added to the report.

---

## Symbol Legend

| Symbol | Meaning |
|---|---|
| ★ | Identical file/folder across all services |
| ✓ | Identical content, different modification time (diverged) |
| ⚠ | Different content, different modification time |
| ⚡ | Different content, same modification time (phantom — dangerous false positive) |
| ◆ | Unique to one service |
| ↪ | Symlink (file-type only; compared by target path) |
| ↪⚠ | Symlink in one service, regular file in another (mixed-type conflict) |

## HTML Report

The HTML report has five sections.

### Section 1: File Counts

A stat grid showing the number of files in each directory and its percentage of the total, followed by a table listing the full path of each directory.

### Section 2: Duplicate File Summary

A table showing per-pair match and version breakdowns across all compared service pairs:

| Column | Description |
|---|---|
| Service Pair | The two services being compared (e.g. `Google Drive↔Dropbox`) |
| Match Type | Color-coded counts: **identical** (green), **different** (red), **unverified** (amber) |
| Version Status | Color-coded counts: **same** (green), **diverged** (amber), **phantom** (red), **mixed-type** (red) |
| Total | All files shared between the pair (duplicates + conflicts) |

Also shows per-service unique file counts (files not duplicated anywhere).

### Section 3: Folder Structure Analysis

Two parts:

- **Part 1 — Folder tree**: collapsible `<details>`/`<summary>` nodes. Each node shows the subtree status symbol (★ = identical subtree, ~ = partially duplicated, ✗ = has conflicts), per-folder file counts, and file-level detail within each expanded folder. Files shared across services are listed under "Shared across services" and annotated with ★/✓/⚠/⚡ per their match status; ⚠ and ⚡ files link to Section 4. Files unique to one service are listed under "Only in &lt;service&gt;" with a ◆ marker.

- **Part 2 — Fully duplicated subtrees** panel: a table listing each `safe_to_delete_roots` entry with a per-service ✓ or — column and a total file count for the subtree. Only shown when at least one fully-identical subtree exists.

### Section 4: Files Requiring Action

Files that share a name across services but require manual review before deletion. This includes:

- Files with `content_match = "different"` — same name and size but differing MD5 checksums.
- Symlinks with `version_status = "target_diverged"` — both services have a symlink at the same path but pointing to different targets (symbol: ↪⚠).
- Mixed-type entries with `content_match = "mixed_type"` — one service has a regular file, another has a symlink at the same name (symbol: ↪⚠).

Sorted by relative path. Columns:

| Column | Description |
|---|---|
| File | Filename |
| Folder | Relative folder path |
| Status | `different · diverged`, `different · phantom`, or `mixed type` (for mixed-type file/symlink conflicts) |
| Per-service columns | Size and modification timestamp for each service |

### Section 5: Duplicate Files

Three subsections:

**Duplicate Files** — A row per confirmed duplicate group (files with `content_match = identical` or `unverified`). Columns:

| Column | Description |
|---|---|
| File | Filename |
| Folder | Relative folder path within the directory |
| Size | Human-readable file size |
| Found in | Which services contain this file |
| Match | `content_match` badge: `identical` (green) or `unverified` (amber) |
| Version | `version_status` badge: `same` (green) or `diverged` (amber) |

**Symlinks** — A row per symlink pair where both services agree on the resolved target. Each row shows the symlink name, relative folder, the resolved target path, and which services contain it. Annotated with the ↪ symbol. Dangling symlinks (no resolved target) are shown with a `—` in the Target column.

**Version-Diverged Files** — Files where `content_match = identical` (or `unverified`) but `version_status = diverged`. Content matches (or was not verified); only the modification timestamp differs beyond the tolerance window. Rows are highlighted amber. Columns:

| Column | Description |
|---|---|
| File | Filename |
| Folder | Relative folder path |
| Size | Human-readable file size |
| Found in | Which services contain this file |
| Newest in | Which service has the most recent copy (bold ★) |
| Age gap (days) | Days between the oldest and newest copy |
| Per-service columns | Modification date/time (UTC) for each service; newest copy shown bold with ★ |

Safe to delete older copies once content is confirmed.

---

## Report Confidentiality

Reports contain complete file paths, directory structures, and file metadata from your compared directories. **Do not share reports publicly or with untrusted parties** without first reviewing the content. Paths may reveal information about your system organization, project structure, or personally-identifiable information.

If you need to share the report with others, consider redacting sensitive paths or filename patterns first.

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

Array of file groups that require manual review before deletion. Entries have `content_match = "different"` (MD5 mismatch) or `content_match = "mixed_type"` (one service has a regular file, another has a symlink at the same name).

Each entry includes `service_details` with per-service `size`, `mtime` (formatted string, e.g. `"2024-01-15 10:00 UTC"`), and `mtime_raw` (Unix timestamp float) fields.

| `version_status` value | Meaning |
|---|---|
| `diverged` | Content differs and timestamps also differ — keep the newer copy |
| `phantom` | Content differs despite matching timestamps — keep both copies |
| `conflict` | Mixed-type entry (file vs symlink) — version comparison not applicable |

### `safe_to_delete_roots`

Array of folder paths whose entire subtree (all descendant folders) is classified `identical` across all compared services. These are the highest-level folders safe to delete — subfolders are omitted since they are already covered by their ancestor.
