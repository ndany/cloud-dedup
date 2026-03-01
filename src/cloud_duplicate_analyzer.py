#!/usr/bin/env python3
r"""
cloud_duplicate_analyzer.py
────────────────────────────────────────────────────────────────────────────
Scans two or more directories and produces an HTML report describing:

  • How many files each directory contains
  • Which files are duplicated across directories (confirmed by MD5 checksum)
  • Content match: identical (MD5 confirmed) | different (conflict) | unverified (--no-checksum)
  • Version status: same (mtime agrees) | diverged (mtime differs) | phantom (mtime agrees but content differs)
  • How folder sub-trees relate, with subtree rollup and safe-to-delete identification

Usage
─────
  python cloud_duplicate_analyzer.py <dir1> <dir2> [dir3 ...] [options]

  Positional arguments:
    dir1, dir2, ...    Paths to the directories to compare.
                       Each path may optionally be prefixed with a label:
                         "Google Drive:/Users/me/Google Drive"
                         "Dropbox:/Users/me/Dropbox"
                       If no label is supplied, the last component of the
                       path is used as the label.

  Options:
    -o, --output FILE      Full path for the output file (stem used for both
                           .html and .json). The filename should include a
                           timestamp if you run this repeatedly.
                           Default: cloud_duplicate_report_YYMMDDHHMM.html
                           in the current directory.
    --output-dir DIR       Directory in which to write the report. The filename
                           is auto-generated as:
                             cloud_duplicate_report_YYMMDDHHMM.html
                           Use this instead of -o when you want a fixed output
                           location without specifying the filename each time.
                           Ignored if -o is also given.
    --mtime-fuzz N         Seconds within which two mtimes are considered equal.
                           Default: 5
    --no-checksum          Skip MD5 checksums. Matches labelled 'unverified';
                           'phantom' conflicts (same metadata, different content)
                           cannot be detected.
    --skip-hidden          Skip files/folders whose name starts with '.' (default).
    --include-hidden       Include hidden files/folders.
    -h, --help             Show this help message and exit.

Examples
────────
  # Minimal — labels inferred from directory names
  python cloud_duplicate_analyzer.py ~/Google\ Drive ~/Dropbox ~/OneDrive

  # Explicit labels and custom output path
  python cloud_duplicate_analyzer.py \\
      "GDrive:~/Google Drive" \\
      "Dropbox:~/Dropbox" \\
      "OneDrive:~/OneDrive" \\
      -o ~/Desktop/dup_report.html

  # Two-way comparison, no checksums
  python cloud_duplicate_analyzer.py ~/Dropbox ~/OneDrive --no-checksum

Requirements
────────────
  Python 3.8+  — no third-party packages required.
"""

import argparse
import hashlib
import html
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path

# ─────────────────────────────────────────────────────── helpers ──

def parse_dir_arg(raw: str):
    """Parse 'Label:/path' or just '/path'. Returns (label, Path)."""
    if ":" in raw and not raw.startswith("/") and not raw.startswith("~"):
        # Could be "Label:/absolute/path" or "C:\..." on Windows
        colon_idx = raw.index(":")
        label = raw[:colon_idx].strip()
        path = Path(raw[colon_idx + 1:].strip()).expanduser().resolve()
    else:
        path = Path(raw).expanduser().resolve()
        label = path.name or str(path)
    return label, path


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n:.1f} PB"


def md5(filepath: Path, chunk=1 << 20) -> str:
    h = hashlib.md5()
    try:
        with open(filepath, "rb") as f:
            while True:
                data = f.read(chunk)
                if not data:
                    break
                h.update(data)
    except (OSError, PermissionError):
        return ""
    return h.hexdigest()


def fmt_ts(ts: float) -> str:
    if ts == 0:
        return "—"
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except (OSError, OverflowError, ValueError):
        return "—"


# ─────────────────────────────────────────────────────── scanning ──

def scan_directory(root: Path, skip_hidden: bool) -> list[dict]:
    """Return a list of file records for all files under root.

    Regular file records contain: rel_path, name, name_orig, size, mtime, full_path, folder, is_symlink=False, symlink_target=None.
    Symlink records contain: rel_path, name, name_orig, size=-1, mtime=0.0, full_path, folder, is_symlink=True, symlink_target (str or None).
    Symlinks are detected with Path.is_symlink() and compared by target path, not content.
    """
    records = []
    for dirpath, dirnames, filenames in os.walk(root):
        if skip_hidden:
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            filenames = [f for f in filenames if not f.startswith(".")]
        for fname in filenames:
            if fname == ".DS_Store":
                continue
            full = Path(dirpath) / fname
            rel = full.relative_to(root)
            if full.is_symlink():
                try:
                    target = full.resolve()
                except (OSError, PermissionError):
                    target = None
                records.append({
                    "rel_path": str(rel),
                    "name": fname.lower(),
                    "name_orig": fname,
                    "size": -1,  # Sentinel: symlinks have no meaningful size; -1 avoids colliding with real empty files
                    "mtime": 0.0,
                    "full_path": full,
                    "folder": str(rel.parent),
                    "is_symlink": True,
                    "symlink_target": str(target) if target is not None else None,
                })
            else:
                try:
                    st = full.stat()
                    size = st.st_size
                    mtime = st.st_mtime
                except (OSError, PermissionError):
                    size, mtime = 0, 0.0
                records.append({
                    "rel_path": str(rel),
                    "name": fname.lower(),           # lower for case-insensitive match
                    "name_orig": fname,
                    "size": size,
                    "mtime": mtime,
                    "full_path": full,
                    "folder": str(rel.parent),
                    "is_symlink": False,
                    "symlink_target": None,
                })
    return records


# ─────────────────────────────────────────────────────── matching ──

def build_name_size_index(records: list[dict]) -> dict:
    idx = defaultdict(list)
    for r in records:
        idx[(r["name"], r["size"])].append(r)
    return idx


def classify_pair(a: dict, b: dict, mtime_fuzz: float, use_checksum: bool):
    """
    Compare two file records that share the same (name, size) index key.

    Returns (content_match, version_status) or None if name/size don't match.

      content_match : 'identical' | 'different' | 'unverified' | 'symlink' | 'mixed_type'
      version_status: 'same'      | 'diverged'  | 'phantom'    | 'target_identical' | 'target_diverged' | 'conflict'

    Special cases for symlinks:
      ("symlink", "target_identical") — both records are symlinks pointing to the same
          resolved target path. Returned only when both targets are non-None.
      ("symlink", "target_diverged")  — both records are symlinks but their targets
          differ, or either target is None (unresolvable / dangling symlink).
      ("mixed_type", "conflict")      — one record is a symlink, the other is a
          regular file; the pair cannot be meaningfully compared.

    'phantom' means mtime agrees but MD5 differs — the most dangerous case:
    the file looks like a safe duplicate but the content is actually different.

    Empty files (size == 0) are always classified ("identical", "same") regardless
    of mtime. There is no content to version, so mtime differences on empty files
    are always sync artifacts rather than meaningful edits.
    """
    # Handle symlinks by target path (not content)
    a_is_symlink = a.get("is_symlink", False)
    b_is_symlink = b.get("is_symlink", False)

    if a_is_symlink != b_is_symlink:
        # Mixed: one service has a file, the other has a symlink
        return ("mixed_type", "conflict")

    if a_is_symlink and b_is_symlink:
        # Both are symlinks: compare by target path string
        a_target = a.get("symlink_target")
        b_target = b.get("symlink_target")
        if a_target is not None and b_target is not None and a_target == b_target:
            return ("symlink", "target_identical")
        else:
            return ("symlink", "target_diverged")

    # Both are regular files: continue to existing logic below
    if a["name"] != b["name"] or a["size"] != b["size"]:
        return None

    mtime_same = abs(a["mtime"] - b["mtime"]) <= mtime_fuzz

    # Empty files: no content to version; mtime differences are always sync artifacts.
    if a["size"] == 0:
        return ("identical", "same")

    if not use_checksum:
        return ("unverified", "same" if mtime_same else "diverged")

    hash_a = md5(a["full_path"])
    hash_b = md5(b["full_path"])

    if not hash_a or not hash_b:
        # Hash failed (permission error etc.) — fall back to mtime only.
        return ("unverified", "same" if mtime_same else "diverged")

    if hash_a == hash_b:
        return ("identical", "same" if mtime_same else "diverged")
    else:
        # 'phantom': content differs despite matching timestamps — keep both copies.
        return ("different", "phantom" if mtime_same else "diverged")


# ─────────────────────────────────────────────────────── core analysis ──

def analyze(dirs: list[tuple[str, Path]], mtime_fuzz: float, use_checksum: bool,
            skip_hidden: bool) -> dict:
    """
    Perform full duplicate analysis. Returns a rich result dict.
    """
    labels = [label for label, _ in dirs]
    n = len(labels)

    print("Scanning directories …")
    scanned = {}
    for label, path in dirs:
        print(f"  [{label}]  {path}")
        scanned[label] = scan_directory(path, skip_hidden)
        print(f"           {len(scanned[label]):,} files found")

    # Build name+size indexes per directory
    indexes = {label: build_name_size_index(recs) for label, recs in scanned.items()}

    # Build rel_path index per directory (for mixed-type symlink/file detection)
    rel_path_indexes: dict[str, dict[str, dict]] = {
        label: {r["rel_path"].lower(): r for r in recs}
        for label, recs in scanned.items()
    }

    # ── find duplicate groups ──────────────────────────────────────
    # A group = same logical file appearing in 2+ directories.
    # Key: (normalised_name, size_bucket) — we match pairwise then cluster.

    print("\nMatching files across directories …")

    # Map: rel_path_lower -> list of (label, record)
    all_keys = set()
    for label, recs in scanned.items():
        for r in recs:
            all_keys.add((r["name"], r["size"]))

    duplicate_groups = []   # content_match: 'identical' or 'unverified'
    conflict_groups  = []   # content_match: 'different'
    symlinks         = []   # content_match: 'symlink' (both records are symlinks)

    for key in all_keys:
        name, size = key
        present_in = {}
        for label in labels:
            hits = indexes[label].get(key, [])
            if hits:
                present_in[label] = hits[0]
        if len(present_in) < 2:
            continue

        label_list = list(present_in.keys())

        # Classify all pairs; the group classification is the worst-case pair.
        # Precedence: different > unverified > identical
        # version precedence: phantom > diverged > same
        content_rank = {"identical": 0, "unverified": 1, "different": 2}
        version_rank = {"same": 0, "diverged": 1, "phantom": 2}
        group_content = "identical"
        group_version = "same"
        all_matched = True

        for la, lb in combinations(label_list, 2):
            result = classify_pair(
                present_in[la], present_in[lb], mtime_fuzz, use_checksum
            )
            if result is None:
                all_matched = False
                break
            cm, vs = result

            if cm == "symlink":
                first_label = next(iter(present_in))
                first_rec = present_in[first_label]
                symlinks.append({
                    "name_orig": first_rec["name_orig"],
                    "rel_path": first_rec["rel_path"],
                    "folder": first_rec.get("folder", "."),
                    "is_symlink": True,
                    "symlink_targets": {
                        label: rec.get("symlink_target")
                        for label, rec in present_in.items()
                    },
                    "symlink_status": vs,
                    "services": list(present_in.keys()),
                })
                all_matched = False  # prevent falling through to group building
                break

            if cm == "mixed_type":
                first_rec = next(iter(present_in.values()))
                conflict_groups.append({
                    "name_orig": first_rec["name_orig"],
                    "rel_path": first_rec["rel_path"],
                    "folder": first_rec.get("folder", "."),
                    "content_match": "mixed_type",
                    "version_status": vs,
                    "matches": present_in,
                    "service_details": {
                        label: {
                            "size":           rec.get("size"),
                            "mtime":          fmt_ts(rec.get("mtime", 0.0)),
                            "mtime_raw":      rec.get("mtime", 0.0),
                            "is_symlink":     rec.get("is_symlink", False),
                            "symlink_target": rec.get("symlink_target"),
                        }
                        for label, rec in present_in.items()
                    },
                    "newest_in": None,           # Not applicable for mixed-type
                    "age_difference_days": None, # Not applicable for mixed-type
                })
                all_matched = False  # prevent falling through to group building
                break

            if content_rank[cm] > content_rank[group_content]:
                group_content = cm
            if version_rank[vs] > version_rank[group_version]:
                group_version = vs

        if not all_matched:
            continue

        rel = present_in[label_list[0]]["rel_path"]
        name_orig = present_in[label_list[0]]["name_orig"]

        service_details = {
            label: {
                "size":      present_in[label]["size"],
                "mtime":     fmt_ts(present_in[label]["mtime"]),
                "mtime_raw": present_in[label]["mtime"],
            }
            for label in present_in
        }

        group = {
            "rel_path":        rel,
            "name_orig":       name_orig,
            "size":            size,
            "matches":         {label: present_in[label] for label in present_in},
            "content_match":   group_content,
            "version_status":  group_version,
            "service_details": service_details,
            "newest_in": (
                max(present_in, key=lambda l: present_in[l]["mtime"])
                if group_version in ("diverged", "phantom") else None
            ),
            "age_difference_days": round(
                (max(r["mtime"] for r in present_in.values()) -
                 min(r["mtime"] for r in present_in.values())) / 86400, 2
            ),
        }

        if group_content == "different":
            conflict_groups.append(group)
        else:
            duplicate_groups.append(group)

    # ── mixed-type detection pass ──────────────────────────────────
    # A symlink in one service and a regular file in another share the same
    # rel_path but differ in size (-1 vs real size), so the (name, size) index
    # never pairs them.  Find them here via the rel_path index.
    already_handled = {g["rel_path"].lower() for g in conflict_groups}
    already_handled |= {g["rel_path"].lower() for g in duplicate_groups}
    already_handled |= {s["rel_path"].lower() for s in symlinks}

    all_rel_paths: set[str] = set()
    for recs in scanned.values():
        for r in recs:
            all_rel_paths.add(r["rel_path"].lower())

    for rp_lower in all_rel_paths:
        if rp_lower in already_handled:
            continue
        present_in = {}
        for label in labels:
            rec = rel_path_indexes[label].get(rp_lower)
            if rec is not None:
                present_in[label] = rec
        if len(present_in) < 2:
            continue
        symlink_labels  = [l for l, r in present_in.items() if r.get("is_symlink")]
        regular_labels  = [l for l, r in present_in.items() if not r.get("is_symlink")]
        if not symlink_labels or not regular_labels:
            continue  # All same type — handled by main loop
        first_rec = next(iter(present_in.values()))
        conflict_groups.append({
            "name_orig":     first_rec["name_orig"],
            "rel_path":      first_rec["rel_path"],
            "folder":        first_rec.get("folder", "."),
            "content_match": "mixed_type",
            "version_status": "conflict",
            "matches":       present_in,
            "service_details": {
                label: {
                    "size":           rec.get("size"),
                    "mtime":          fmt_ts(rec.get("mtime", 0.0)),
                    "mtime_raw":      rec.get("mtime", 0.0),
                    "is_symlink":     rec.get("is_symlink", False),
                    "symlink_target": rec.get("symlink_target"),
                }
                for label, rec in present_in.items()
            },
            "newest_in":           None,
            "age_difference_days": None,
        })

    # Build lookup for folder tree renderer (Task 6):
    # (name_lower, folder_str) → {content_match, version_status, conflict_index}
    _file_classifications = {}
    conflict_groups.sort(key=lambda g: g["rel_path"])
    for i, g in enumerate(conflict_groups):
        rp = Path(g["rel_path"])
        folder = str(rp.parent) if str(rp.parent) != "." else "(root)"
        _file_classifications[(g["name_orig"].lower(), folder)] = {
            "content_match":  g["content_match"],
            "version_status": g["version_status"],
            "conflict_index": i,
            "is_symlink":     False,
            "symlink_target": None,
        }
    for g in duplicate_groups:
        rp = Path(g["rel_path"])
        folder = str(rp.parent) if str(rp.parent) != "." else "(root)"
        key = (g["name_orig"].lower(), folder)
        if key not in _file_classifications:
            _file_classifications[key] = {
                "content_match":  g["content_match"],
                "version_status": g["version_status"],
                "conflict_index": None,
                "is_symlink":     False,
                "symlink_target": None,
            }
    for s in symlinks:
        rp = Path(s["rel_path"])
        folder = str(rp.parent) if str(rp.parent) != "." else "(root)"
        key = (s["name_orig"].lower(), folder)
        # Pick a representative symlink target from the first service that has one
        target = next((v for v in s.get("symlink_targets", {}).values() if v), None)
        if key not in _file_classifications:
            _file_classifications[key] = {
                "content_match":  "symlink",
                "version_status": s.get("symlink_status", "target_identical"),
                "conflict_index": None,
                "is_symlink":     True,
                "symlink_target": target,
            }

    # ── pairwise duplicate counts ─────────────────────────────────
    pairwise_counts = {}
    for la, lb in combinations(labels, 2):
        count = sum(1 for g in duplicate_groups if la in g["matches"] and lb in g["matches"])
        pairwise_counts[(la, lb)] = count
    all_three_count = sum(1 for g in duplicate_groups if len(g["matches"]) == n)

    # ── unique files per service ──────────────────────────────────
    dup_rel_paths = defaultdict(set)
    for g in duplicate_groups + conflict_groups:
        for label in g["matches"]:
            dup_rel_paths[label].add(g["rel_path"].lower())
    for s in symlinks:
        for label in s.get("services", []):
            dup_rel_paths[label].add(s["rel_path"].lower())

    unique_counts = {}
    unique_files  = {}
    for label, recs in scanned.items():
        uniq = [r for r in recs if r["rel_path"].lower() not in dup_rel_paths[label]]
        unique_counts[label] = len(uniq)
        unique_files[label]  = uniq

    # ── folder-level analysis ─────────────────────────────────────
    print("Analysing folder structure …")

    def folder_file_set(recs):
        fd = defaultdict(set)
        for r in recs:
            fd[r["folder"]].add(r["name"])
        return fd

    folder_sets = {label: folder_file_set(scanned[label]) for label in labels}
    all_folders = set()
    for fd in folder_sets.values():
        all_folders.update(fd.keys())

    # Also add all ancestor directories so that intermediate parent folders
    # (which contain no files directly) appear in folder_comparisons and
    # can participate in subtree rollup.
    for folder in list(all_folders):
        parts = folder.replace("\\", "/").split("/")
        for depth in range(1, len(parts)):
            parent = "/".join(parts[:depth])
            if parent and parent != ".":
                all_folders.add(parent)

    folder_comparisons = []
    for folder in sorted(all_folders):
        # A label is "present" in a folder if it has files directly there OR
        # has files in any descendant directory.
        def label_has_presence(label, folder):
            if folder in folder_sets[label]:
                return True
            prefix = folder + "/"
            return any(f.startswith(prefix) for f in folder_sets[label])
        present = [l for l in labels if label_has_presence(l, folder)]
        if len(present) < 2:
            continue
        # For file-set comparisons at this level, only use files directly here.
        sets_here = {l: folder_sets[l].get(folder, set()) for l in present}

        # Determine relationship
        sets_list = list(sets_here.values())
        if all(s == sets_list[0] for s in sets_list):
            relationship = "identical"
        else:
            # Check subset/superset among all pairs
            relationships = set()
            for la, lb in combinations(present, 2):
                sa, sb = sets_here[la], sets_here[lb]
                if sa == sb:
                    relationships.add("identical")
                elif sa < sb:
                    relationships.add("subset")
                elif sa > sb:
                    relationships.add("superset")
                else:
                    relationships.add("overlap")
            if relationships == {"identical"}:
                relationship = "identical"
            elif "overlap" in relationships:
                relationship = "overlap"
            elif "subset" in relationships or "superset" in relationships:
                relationship = "subset/superset"
            else:
                relationship = "overlap"

        # Compute per-service unique and shared sets
        all_in_folder = set.union(*sets_here.values())
        in_all = set.intersection(*sets_here.values())

        details = {"in_all": sorted(in_all)}
        for label in present:
            only = sets_here[label] - set.union(*(sets_here[l] for l in present if l != label))
            details[f"{label}_only"] = sorted(only)

        # Pairwise shared (not in all)
        for la, lb in combinations(present, 2):
            in_pair = (sets_here[la] & sets_here[lb]) - in_all
            if in_pair:
                details[f"{la}+{lb}"] = sorted(in_pair)

        folder_comparisons.append({
            "folder_path": folder if folder != "." else "(root)",
            "services_present": present,
            "relationship": relationship,
            "total_unique_files": len(all_in_folder),
            "files_in_all": len(in_all),
            "details": details,
        })

    rel_counts = defaultdict(int)
    for fc in folder_comparisons:
        rel_counts[fc["relationship"]] += 1

    # ── subtree rollups ───────────────────────────────────────────
    fc_by_path = {fc["folder_path"]: fc for fc in folder_comparisons}
    all_fc_paths = set(fc_by_path.keys())

    for fc in folder_comparisons:
        path = fc["folder_path"]
        # Descendants = self + any fc whose path starts with path + "/"
        if path == "(root)":
            descendants = list(fc_by_path.values())
        else:
            descendants = [
                fc_by_path[p] for p in all_fc_paths
                if p == path or p.startswith(path + "/")
            ]

        all_identical = all(d["relationship"] == "identical" for d in descendants)
        any_overlap   = any(d["relationship"] == "overlap"   for d in descendants)

        if all_identical:
            fc["subtree_status"] = "identical"
        elif any_overlap:
            fc["subtree_status"] = "overlap"
        else:
            fc["subtree_status"] = "partial"

        fc["subtree_total_files"] = sum(d["total_unique_files"] for d in descendants)

    # ── safe-to-delete roots ──────────────────────────────────────
    # Highest-level folders whose entire subtree is identical.
    # Exclude folders whose ancestor is also an identical-subtree root.
    identical_fcs = [fc for fc in folder_comparisons if fc["subtree_status"] == "identical"]
    safe_to_delete_roots = []
    for fc in identical_fcs:
        path = fc["folder_path"]
        has_identical_ancestor = any(
            path != other["folder_path"] and path.startswith(other["folder_path"] + "/")
            for other in identical_fcs
        )
        if not has_identical_ancestor:
            safe_to_delete_roots.append(fc)

    return {
        "labels": labels,
        "dirs": {label: str(path) for label, path in dirs},
        "total_files": {label: len(recs) for label, recs in scanned.items()},
        "duplicate_groups": duplicate_groups,
        "conflict_groups":        conflict_groups,
        "symlinks":               symlinks,
        "_file_classifications":  _file_classifications,
        "_scanned_records":       {label: scanned[label] for label in labels},
        "unique_counts": unique_counts,
        "pairwise_counts": {f"{la}↔{lb}": v for (la, lb), v in pairwise_counts.items()},
        "all_services_count": all_three_count,
        "folder_comparisons": folder_comparisons,
        "relationship_counts": dict(rel_counts),
        "safe_to_delete_roots": safe_to_delete_roots,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


# ─────────────────────────────────────────────────────── HTML report ──


def _file_sym(content_match: str, version_status: str, is_symlink: bool = False) -> tuple:
    """Returns (symbol_char, css_class) for a file classification."""
    if is_symlink:
        return ("↪", "sym-symlink")
    if content_match == "mixed_type":
        return ("↪⚠", "sym-dd")
    if content_match in ("identical", "unverified") and version_status == "same":
        return ("★", "sym-is")
    if content_match in ("identical", "unverified") and version_status == "diverged":
        return ("✓", "sym-id")
    if content_match == "different" and version_status == "diverged":
        return ("⚠", "sym-dd")
    if content_match == "different" and version_status == "phantom":
        return ("⚡", "sym-dp")
    return ("~", "sym-id")


def _build_folder_tree(folder_comparisons: list) -> dict:
    """
    Build a nested dict from flat folder_comparisons.
    Each node: {"_fc": fc_or_None, "_children": {name: node}}
    Sorted insertion; root nodes have no "/" in their folder_path.
    """
    tree = {}
    for fc in sorted(folder_comparisons, key=lambda x: x["folder_path"]):
        path = fc["folder_path"]
        if path == "(root)":
            tree.setdefault("(root)", {"_fc": None, "_children": {}})
            tree["(root)"]["_fc"] = fc
            continue
        parts = path.split("/")
        node = tree
        for part in parts[:-1]:
            node = node.setdefault(part, {"_fc": None, "_children": {}})["_children"]
        leaf = parts[-1]
        if leaf not in node:
            node[leaf] = {"_fc": None, "_children": {}}
        node[leaf]["_fc"] = fc
    return tree

CSS = """
body { font-family: Arial, sans-serif; font-size: 14px; color: #1a1a1a;
       max-width: 1100px; margin: 40px auto; padding: 0 20px; }
h1   { font-size: 26px; color: #2E5C8A; border-bottom: 3px solid #2E5C8A;
       padding-bottom: 8px; }
h2   { font-size: 18px; color: #2E5C8A; margin-top: 36px;
       border-bottom: 1px solid #c5d8ec; padding-bottom: 4px; }
h3   { font-size: 15px; color: #2E5C8A; margin-top: 24px; }
table { border-collapse: collapse; width: 100%; margin: 14px 0; font-size: 13px; }
th    { background: #D5E8F0; text-align: left; padding: 8px 10px;
        border: 1px solid #b0ccdd; }
td    { padding: 7px 10px; border: 1px solid #dde; vertical-align: top; }
tr:nth-child(even) td { background: #f4f8fc; }
.badge { display:inline-block; padding:2px 8px; border-radius:12px;
         font-size:11px; font-weight:bold; }
.badge-identical  { background:#d4edda; color:#155724; }
.badge-unverified { background:#e2e3e5; color:#383d41; }
.badge-overlap    { background:#fff3cd; color:#856404; }
.badge-subset     { background:#d1ecf1; color:#0c5460; }
.badge-superset   { background:#d1ecf1; color:#0c5460; }
.badge-diverged   { background:#f8d7da; color:#721c24; }
.badge-same       { background:#d4edda; color:#155724; }
.action-row    { }
.phantom-row td { background:#fff8e1 !important; }
.conflict-row td { background:#fff0f0 !important; }
.service-detail { font-size:12px; line-height:1.6; }
.badge-phantom  { background:#fff3cd; color:#856404; }
.badge-different { background:#f8d7da; color:#721c24; }
.stat-grid { display:grid; grid-template-columns: repeat(auto-fit, minmax(180px,1fr));
             gap:16px; margin:20px 0; }
.stat-card { background:#f0f6fc; border:1px solid #c5d8ec; border-radius:8px;
             padding:16px; text-align:center; }
.stat-card .num { font-size:32px; font-weight:bold; color:#2E5C8A; }
.stat-card .lbl { font-size:12px; color:#555; margin-top:4px; }
details summary { cursor:pointer; font-weight:bold; padding:6px 0;
                  color: #2E5C8A; }
details { margin: 6px 0; }
code { background:#f0f0f0; padding:1px 4px; border-radius:3px; font-size:12px; }
.footer { margin-top:60px; font-size:12px; color:#888; text-align:center;
          border-top:1px solid #ddd; padding-top:12px; }
.tree-node > details { margin-left: 20px; border-left: 2px solid #e0e8f0;
                       padding-left: 8px; }
.tree-node summary { list-style: none; cursor: pointer; padding: 4px 0;
                     user-select: none; }
.tree-node summary::-webkit-details-marker { display: none; }
.tree-file { font-size: 12px; font-family: monospace; padding: 2px 0 2px 24px; }
.tree-file-section { font-size: 11px; font-weight: bold; color: #555;
                     margin: 6px 0 2px 12px; padding-bottom: 2px;
                     border-bottom: 1px solid #eee; }
.sym-is { color: #28a745; }
.sym-id { color: #17a2b8; }
.sym-dd { color: #dc3545; }
.sym-dp { color: #fd7e14; }
.sym-uniq { color: #ff9900; font-weight: bold; }
.sym-symlink { color: #0066cc; font-weight: bold; }
.badge-symlink { background-color: #e6f2ff; color: #0066cc; border: 1px solid #0066cc; }
"""

def badge(text, cls=None):
    cls = cls or text.lower().replace("/", "-").replace(" ", "-")
    return f'<span class="badge badge-{cls}">{html.escape(text)}</span>'


def render_html(result: dict) -> str:
    labels = result["labels"]
    n = len(labels)
    dups = result["duplicate_groups"]
    divs = [g for g in dups if g["version_status"] == "diverged"]
    total = sum(result["total_files"].values())

    parts = [f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Cloud Storage Duplicate Analysis</title>
<style>{CSS}</style></head><body>
<h1>Cloud Storage Duplicate Analysis</h1>
<p style="color:#555">Generated: {result['generated_at']} &nbsp;·&nbsp;
Comparing {n} directories</p>

<h2>1. File Counts</h2>
<div class="stat-grid">
"""]
    for label in labels:
        cnt = result["total_files"][label]
        pct = 100 * cnt / total if total else 0
        parts.append(f'<div class="stat-card"><div class="num">{cnt:,}</div>'
                     f'<div class="lbl">{html.escape(label)}<br>{pct:.1f}% of all files</div></div>')
    parts.append(f'<div class="stat-card"><div class="num">{total:,}</div>'
                 f'<div class="lbl">Total files</div></div>')
    parts.append("</div>")

    # directories table
    parts.append('<table><tr><th>Label</th><th>Path</th><th>Files</th></tr>')
    for label in labels:
        parts.append(f'<tr><td><strong>{html.escape(label)}</strong></td>'
                     f'<td><code>{html.escape(result["dirs"][label])}</code></td>'
                     f'<td>{result["total_files"][label]:,}</td></tr>')
    parts.append("</table>")

    # ── section 2: duplicate summary ──
    parts.append("<h2>2. Duplicate File Summary</h2>")
    parts.append('<table><tr><th>Service Pair</th><th>Duplicate Files</th></tr>')
    for pair, cnt in result["pairwise_counts"].items():
        parts.append(f'<tr><td>{html.escape(pair)}</td><td>{cnt:,}</td></tr>')
    if n > 2:
        parts.append(f'<tr><td><strong>All {n} services</strong></td>'
                     f'<td><strong>{result["all_services_count"]:,}</strong></td></tr>')
    total_dup_instances = sum(result["pairwise_counts"].values())
    parts.append(f'<tr><td><em>Unique files</em></td>')
    unique_str = " | ".join(
        f'{html.escape(l)}: {result["unique_counts"][l]:,}' for l in labels)
    parts.append(f'<td><em>{unique_str}</em></td></tr>')
    parts.append("</table>")
    parts.append(f"<p>Duplicate matching used: same filename + same size, "
                 f"or same filename + modification time within "
                 f"{result.get('mtime_fuzz', 5)} seconds. "
                 f"MD5 checksums were computed for all candidate pairs.</p>")

    # ── section 3: folder structure analysis ──
    fc_list        = result["folder_comparisons"]
    safe_roots     = result.get("safe_to_delete_roots", [])
    file_cls       = result.get("_file_classifications", {})
    scanned_recs   = result.get("_scanned_records", {})

    parts.append(f"<h2>3. Folder Structure Analysis ({len(fc_list)} shared folders)</h2>")

    # ── part 1: folder tree ──
    parts.append("<h3>Folder tree</h3>")
    parts.append(
        "<p>Expand any folder to see file-level detail. "
        "★ = fully identical subtree; ~ = partially duplicated; ✗ = has conflicts; "
        "&#9670; = unique to one service; &#8618; = symlink.</p>"
    )

    # Build per-folder file list: folder_str → {label → [name_orig]}
    folder_label_names: dict = defaultdict(lambda: defaultdict(list))
    for label, recs in scanned_recs.items():
        for r in recs:
            folder_key = r["folder"] if r["folder"] != "." else "(root)"
            folder_label_names[folder_key][label].append(r["name"])  # already lowercased

    def render_node(name: str, node: dict) -> list:
        fc = node.get("_fc")
        children = node.get("_children", {})
        out = []
        if fc is None and not children:
            return out

        ss = fc["subtree_status"] if fc else "partial"
        node_sym, node_cls = {
            "identical": ("★", "sym-is"),
            "partial":   ("~", "sym-id"),
            "overlap":   ("✗", "sym-dd"),
        }.get(ss, ("?", ""))

        file_ct     = fc["total_unique_files"] if fc else 0
        subtree_ct  = fc["subtree_total_files"] if fc else 0
        child_ct    = len(children)
        folder_path = fc["folder_path"] if fc else name

        summary_html = (
            f'<span class="{node_cls}">{node_sym}</span> '
            f'<strong>{html.escape(name)}/</strong>'
            f'&nbsp;<span style="color:#888;font-size:12px">'
            f'{html.escape(ss)}'
            + (f' &nbsp;·&nbsp; {file_ct} files' if file_ct else '')
            + (f' &nbsp;·&nbsp; {child_ct} subfolders' if child_ct else '')
            + (f' &nbsp;·&nbsp; {subtree_ct} total' if child_ct and subtree_ct != file_ct else '')
            + '</span>'
        )

        out.append(f'<div class="tree-node"><details><summary>{summary_html}</summary>')

        # File list for this folder
        if fc:
            fpath = fc["folder_path"]
            per_label = folder_label_names.get(fpath, {})

            # Collect all unique filenames in this folder across all services
            all_names = set()
            for names in per_label.values():
                all_names.update(names)

            in_multiple = []
            unique_to: dict = defaultdict(list)

            for fname in sorted(all_names):
                labels_with = [l for l in labels if fname in per_label.get(l, [])]
                if len(labels_with) >= 2:
                    cls_info = file_cls.get((fname, fpath))
                    in_multiple.append((fname, cls_info))
                elif labels_with:
                    unique_to[labels_with[0]].append(fname)

            if in_multiple:
                out.append('<div class="tree-file-section">Shared across services</div>')
                for fname, cls_info in in_multiple:
                    if cls_info:
                        is_sym = cls_info.get("is_symlink", False)
                        sym, sym_cls = _file_sym(
                            cls_info["content_match"], cls_info["version_status"],
                            is_symlink=is_sym
                        )
                        link = ""
                        if cls_info.get("conflict_index") is not None:
                            link = (
                                f' <a href="#action-{cls_info["conflict_index"]}" '
                                f'style="font-size:10px;color:#888">&rarr;&nbsp;&sect;4</a>'
                            )
                        target_span = ""
                        if is_sym and cls_info.get("symlink_target"):
                            target_span = (
                                f' <span style="font-size:11px;color:#888">'
                                f'&rarr; {html.escape(cls_info["symlink_target"])}</span>'
                            )
                        out.append(
                            f'<div class="tree-file">'
                            f'<span class="{sym_cls}">{sym}</span> '
                            f'{html.escape(fname)}{target_span}{link}</div>'
                        )
                    else:
                        out.append(
                            f'<div class="tree-file">· {html.escape(fname)}</div>'
                        )

            for label in labels:
                ufiles = unique_to.get(label, [])
                if ufiles:
                    out.append(
                        f'<div class="tree-file-section">'
                        f'Only in {html.escape(label)}</div>'
                    )
                    for fname in ufiles:
                        out.append(
                            f'<div class="tree-file">'
                            f'<span class="sym-uniq">&#9670;</span> '
                            f'{html.escape(fname)}</div>'
                        )

        # Recurse into children
        for child_name in sorted(children):
            out.extend(render_node(child_name, children[child_name]))

        out.append("</details></div>")
        return out

    tree = _build_folder_tree(fc_list)
    parts.append('<div style="margin:12px 0">')
    for root_name in sorted(tree):
        parts.extend(render_node(root_name, tree[root_name]))
    parts.append("</div>")

    parts.append(
        "<p style='font-size:12px;color:#888;margin-top:12px'>"
        "★ identical&nbsp;·&nbsp;same &nbsp;|&nbsp; "
        "✓ identical&nbsp;·&nbsp;diverged &nbsp;|&nbsp; "
        "⚠ different&nbsp;·&nbsp;diverged &nbsp;|&nbsp; "
        "⚡ different&nbsp;·&nbsp;phantom &nbsp;|&nbsp; "
        "&#9670; unique to one service &nbsp;|&nbsp; "
        "&#8618; symlink"
        "</p>"
    )

    # ── part 2: actionability panel ──
    parts.append("<h3>Fully duplicated subtrees — safe to delete</h3>")
    if not safe_roots:
        parts.append(
            "<p>No folder subtrees are fully identical across all services.</p>"
        )
    else:
        parts.append(
            "<p>Each subtree below is 100% identical across all copies. "
            "Deleting from any one service is safe.</p>"
        )
        svc_hdrs = "".join(f'<th>{html.escape(l)}</th>' for l in labels)
        parts.append(
            f'<table><tr><th>Folder</th>{svc_hdrs}<th>Files in subtree</th></tr>'
        )
        for fc in sorted(safe_roots, key=lambda x: x["folder_path"]):
            svc_cells = "".join(
                '<td style="color:#28a745;font-weight:bold">✓</td>'
                if l in fc["services_present"] else
                '<td style="color:#aaa">—</td>'
                for l in labels
            )
            parts.append(
                f'<tr>'
                f'<td><code>{html.escape(fc["folder_path"])}</code></td>'
                f'{svc_cells}'
                f'<td>{fc["subtree_total_files"]:,}</td>'
                f'</tr>'
            )
        parts.append("</table>")

    # ── section 4: files requiring action ──
    conflicts = result.get("conflict_groups", [])
    diverged_symlinks = [s for s in result.get("symlinks", []) if s.get("symlink_status") == "target_diverged"]
    total_action_items = len(conflicts) + len(diverged_symlinks)
    parts.append(f'<h2 id="s4">4. Files Requiring Action ({total_action_items})</h2>')
    if not conflicts and not diverged_symlinks:
        parts.append(
            "<p>No content conflicts found — all matched files have identical content "
            "(or matching was skipped with <code>--no-checksum</code>).</p>"
        )
    else:
        parts.append(
            "<p>These files share a name and size across services but have "
            "<strong>different content</strong>. Review each before deleting any copy.</p>"
            "<p>"
            "<strong>⚠ different&nbsp;·&nbsp;diverged</strong> — content differs, "
            "timestamps differ; keep the newer copy.<br>"
            "<strong>⚡ different&nbsp;·&nbsp;phantom</strong> — content differs despite "
            "matching timestamps; keep both copies.<br>"
            "<strong>&#8618; mixed type</strong> — one service has a regular file and "
            "another has a symlink with the same name.<br>"
            "<strong>&#8618; target_diverged</strong> — both services have a symlink with "
            "the same name but pointing to different targets.</p>"
        )
        if conflicts:
            svc_headers = "".join(
                f'<th>{html.escape(l)}</th>' for l in labels
            )
            parts.append(
                f'<table><tr><th>File</th><th>Folder</th><th>Status</th>{svc_headers}</tr>'
            )
            for i, g in enumerate(sorted(conflicts, key=lambda x: x["rel_path"])):
                rp = Path(g["rel_path"])
                folder_str = str(rp.parent) if str(rp.parent) != "." else "(root)"
                cm = g.get("content_match", "different")
                vs = g["version_status"]
                if cm == "mixed_type":
                    symbol = "&#8618;"
                    row_cls = "conflict-row"
                    status_parts = ["mixed&nbsp;type"]
                else:
                    symbol = "⚡" if vs == "phantom" else "⚠"
                    row_cls = "phantom-row" if vs == "phantom" else "conflict-row"
                    status_parts = [f"different&nbsp;·&nbsp;{html.escape(vs)}"]
                    if vs == "diverged" and g.get("newest_in"):
                        status_parts.append(
                            f'<br><span style="font-size:11px;color:#666">'
                            f'newer in {html.escape(g["newest_in"])}</span>'
                        )

                svc_cells = ""
                for label in labels:
                    det = g["service_details"].get(label)
                    if det:
                        if det.get("is_symlink"):
                            tgt = det.get("symlink_target") or "—"
                            svc_cells += (
                                f'<td class="service-detail">'
                                f'&#8618; symlink<br>'
                                f'<span style="font-size:11px;color:#666">'
                                f'&rarr; {html.escape(str(tgt))}</span></td>'
                            )
                        else:
                            size_val = det.get("size", 0)
                            size_str = human_size(size_val) if size_val is not None and size_val >= 0 else "—"
                            svc_cells += (
                                f'<td class="service-detail">'
                                f'{size_str}<br>'
                                f'{html.escape(det["mtime"])}</td>'
                            )
                    else:
                        svc_cells += '<td style="color:#aaa">—</td>'

                extra_note = ""
                if cm == "mixed_type":
                    extra_note = (
                        '<tr class="conflict-row">'
                        f'<td colspan="{3 + len(labels)}" style="font-size:12px;color:#666;'
                        f'font-style:italic;padding:4px 10px">'
                        'One service has a regular file and another has a symlink with the same name. '
                        'Cannot safely deduplicate without understanding your backup strategy.'
                        '</td></tr>'
                    )

                parts.append(
                    f'<tr class="{row_cls}" id="action-{i}">'
                    f'<td><strong>{symbol} {html.escape(g["name_orig"])}</strong></td>'
                    f'<td><code>{html.escape(folder_str)}</code></td>'
                    f'<td>{"".join(status_parts)}</td>'
                    f'{svc_cells}</tr>'
                    + extra_note
                )
            parts.append("</table>")
        # Render target_diverged symlinks in Section 4
        if diverged_symlinks:
            parts.append('<h3>Diverged Symlinks</h3>')
            parts.append(
                '<p>These symlinks point to different targets across services. '
                'Review before deleting to avoid losing references.</p>'
            )
            for sym in sorted(diverged_symlinks, key=lambda x: x["rel_path"]):
                parts.append(
                    f'<div class="conflict-row">'
                    f'<span class="sym-symlink">&#8618;</span> '
                    f'<strong>{html.escape(sym["name_orig"])}</strong>'
                    f'<span style="color:#888;margin-left:8px">{html.escape(sym.get("folder", ""))}</span>'
                    f'</div>'
                )
                parts.append('<table>')
                parts.append('<tr><th>Service</th><th>Symlink Target</th></tr>')
                for label, target in sym.get("symlink_targets", {}).items():
                    target_str = html.escape(str(target)) if target else '<em>unresolvable</em>'
                    parts.append(f'<tr><td>{html.escape(label)}</td><td><code>{target_str}</code></td></tr>')
                parts.append('</table>')

    # ── section 5: duplicate file list ──
    parts.append(f"<h2>5. Duplicate Files ({len(dups)} confirmed)</h2>")
    if not dups:
        parts.append("<p>No duplicate files found.</p>")
    else:
        parts.append('<table><tr><th>File</th><th>Folder</th><th>Size</th>'
                     '<th>Found in</th><th>Match</th></tr>')
        for g in sorted(dups, key=lambda x: x["rel_path"]):
            found_in = ", ".join(g["matches"].keys())
            match_label = f'{g.get("content_match", "unverified")} · {g.get("version_status", "same")}'
            match_cell  = badge(match_label, g.get("content_match", "unverified"))
            # folder = parent of rel_path
            rp = Path(g["rel_path"])
            folder_str = str(rp.parent) if str(rp.parent) != "." else "(root)"
            parts.append(f'<tr>'
                         f'<td>{html.escape(g["name_orig"])}</td>'
                         f'<td><code>{html.escape(folder_str)}</code></td>'
                         f'<td style="white-space:nowrap">{human_size(g["size"])}</td>'
                         f'<td>{html.escape(found_in)}</td>'
                         f'<td>{match_cell}</td></tr>')
        parts.append("</table>")

    # Symlinks subsection
    symlinks_data = result.get("symlinks", [])
    if symlinks_data:
        parts.append(f'<h3>Symlinks ({len(symlinks_data)})</h3>')
        parts.append(
            '<table><tr><th>Name</th><th>Target</th><th>Status</th><th>Services</th></tr>'
        )
        for sym in sorted(symlinks_data, key=lambda x: x["rel_path"]):
            targets = sym.get("symlink_targets", {})
            target_display = next((v for v in targets.values() if v), "—")
            status = sym.get("symlink_status", "unknown")
            services_list = sym.get("services", [])
            services = ", ".join(services_list)
            parts.append(
                f'<tr>'
                f'<td><strong>&#8618; {html.escape(sym["name_orig"])}</strong><br>'
                f'<small style="color:#888">{html.escape(sym.get("folder", ""))}</small></td>'
                f'<td><code style="font-size:11px">'
                f'{html.escape(str(target_display))}</code></td>'
                f'<td>{badge(f"symlink · {status}", "symlink")}</td>'
                f'<td>{html.escape(services)}</td>'
                f'</tr>'
            )
        parts.append('</table>')

    # (added in Task 3)

    # ── footer ──
    parts.append(f'<div class="footer">Cloud Storage Duplicate Analysis · '
                 f'{result["generated_at"]} · '
                 f'cloud_duplicate_analyzer.py</div></body></html>')

    return "\n".join(parts)


# ─────────────────────────────────────────────────────── CLI ──

def main():
    p = argparse.ArgumentParser(
        description="Compare cloud storage directories and produce a duplicate analysis report.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("dirs", nargs="+",
                   help='Directories to compare. Prefix with "Label:" to set a display name.')
    p.add_argument("-o", "--output", default=None,
                   help="Full output file path (stem shared by .html and .json). "
                        "Defaults to cloud_duplicate_report_YYMMDDHHMM.html in the current dir.")
    p.add_argument("--output-dir", default=None,
                   help="Directory for auto-named output files. "
                        "Ignored when -o is specified.")
    p.add_argument("--mtime-fuzz", type=float, default=5,
                   help="Seconds tolerance for mtime comparison (default: 5)")
    p.add_argument("--no-checksum", action="store_true",
                   help="Skip MD5 checksums. Matches are labelled 'unverified' rather than "
                        "'identical'. The 'phantom' conflict case (same metadata, different "
                        "content) cannot be detected without checksums.")
    p.add_argument("--include-hidden", action="store_true",
                   help="Include hidden files and folders (names starting with '.')")

    args = p.parse_args()

    if len(args.dirs) < 2:
        p.error("Please provide at least two directories to compare.")

    parsed_dirs = []
    for raw in args.dirs:
        label, path = parse_dir_arg(raw)
        if not path.exists():
            p.error(f"Directory not found: {path}")
        if not path.is_dir():
            p.error(f"Not a directory: {path}")
        parsed_dirs.append((label, path))

    result = analyze(
        dirs=parsed_dirs,
        mtime_fuzz=args.mtime_fuzz,
        use_checksum=not args.no_checksum,
        skip_hidden=not args.include_hidden,
    )
    result["mtime_fuzz"] = args.mtime_fuzz

    # Resolve output path
    ts = datetime.now().strftime("%y%m%d%H%M")
    auto_name = f"cloud_duplicate_report_{ts}.html"
    if args.output:
        output_path = Path(args.output).expanduser().resolve()
    elif args.output_dir:
        output_path = Path(args.output_dir).expanduser().resolve() / auto_name
    else:
        output_path = Path.cwd() / auto_name
    output_path.parent.mkdir(parents=True, exist_ok=True)

    report_html = render_html(result)
    output_path.write_text(report_html, encoding="utf-8")

    # Also save raw JSON alongside the HTML for programmatic use
    json_path = output_path.with_suffix(".json")
    with open(json_path, "w", encoding="utf-8") as f:
        # Remove full_path (Path objects) and private implementation keys before serialising
        _PRIVATE_KEYS = {"_file_classifications", "_scanned_records"}
        clean = json.loads(json.dumps(
            {k: v for k, v in result.items() if k not in _PRIVATE_KEYS},
            default=str
        ))
        json.dump(clean, f, indent=2)

    print(f"\n✓ HTML report → {output_path}")
    print(f"✓ JSON data   → {json_path}")

    # Print a quick summary to stdout
    labels = result["labels"]
    dups = result["duplicate_groups"]
    print(f"\nSummary")
    print(f"  Total files : {sum(result['total_files'].values()):,}")
    for l in labels:
        print(f"  {l:20s}: {result['total_files'][l]:,} files  "
              f"({result['unique_counts'][l]:,} unique)")
    print(f"\n  Duplicate groups : {len(dups):,}")
    for pair, cnt in result["pairwise_counts"].items():
        print(f"    {pair}: {cnt:,}")
    if len(labels) > 2:
        print(f"    All {len(labels)} services: {result['all_services_count']:,}")
    conflicts = result.get("conflict_groups", [])
    if conflicts:
        print(f"\n  ⚠  {len(conflicts)} file(s) require action (different content) — see Section 4 of report")
    sym_list = result.get("symlinks", [])
    div_sym_count = sum(1 for s in sym_list if s.get("symlink_status") == "target_diverged")
    if sym_list:
        print(f"  ↪  {len(sym_list)} symlink(s) detected"
              + (f" ({div_sym_count} with diverged targets — see Section 4)" if div_sym_count else ""))
    rc = result["relationship_counts"]
    print(f"\n  Folder relationships:")
    for rel, cnt in sorted(rc.items()):
        print(f"    {rel:20s}: {cnt}")


if __name__ == "__main__":
    main()
