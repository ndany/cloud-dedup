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

### Section 3: Duplicate Files

A row per confirmed duplicate group. Columns:

| Column | Description |
|---|---|
| File | Filename |
| Folder | Relative folder path within the directory |
| Size | Human-readable file size |
| Found in | Which services contain this file |
| Match | `exact` or `likely` — see [how-it-works.md](how-it-works.md) |
| Version | `same` (timestamps agree) or `diverged` (timestamps differ — row highlighted yellow) |

### Section 4: Version-Diverged Files

A filtered view showing only the files from Section 3 where version status is `diverged`. Sorted by age gap (largest first). Columns:

| Column | Description |
|---|---|
| File | Filename |
| Path | Relative folder path |
| Newest version in | Which service holds the most recently modified copy |
| Age gap (days) | Days between the oldest and newest copy |
| Copy dates | Modification timestamp of each copy |

### Section 5: Folder Structure Analysis

Stat cards showing counts of identical / subset-superset / overlap folders, then three sub-tables:

- **Identical folders** — folder path, services present, file count
- **Subset/superset folders** — which service has extra files and how many
- **Overlapping folders** — for each service, what files are exclusive to it

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
        "Dropbox":      { ... },
        "OneDrive":     { ... }
      },
      "confidence": "exact",
      "version_status": "same",
      "newest_in": null,
      "age_difference_days": 0.0,
      "copy_mtimes": {
        "Google Drive": "2021-03-15 18:42 UTC",
        "Dropbox":      "2021-03-15 18:42 UTC",
        "OneDrive":     "2021-03-15 18:42 UTC"
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
  "relationship_counts": {
    "identical": 48,
    "overlap": 6,
    "subset/superset": 8
  },
  "generated_at": "2026-02-28 14:30",
  "mtime_fuzz": 5
}
```

### `duplicate_groups[].confidence`

| Value | Meaning |
|---|---|
| `exact` | Name + size + mtime all agree (or MD5 confirmed) |
| `likely` | Name + size agree; mtime differs but MD5 matches (or checksums skipped) |

### `duplicate_groups[].version_status`

| Value | Meaning |
|---|---|
| `same` | All copies have mtimes within the fuzz window |
| `diverged` | At least one copy has a mtime more than `mtime_fuzz` seconds away from another |
