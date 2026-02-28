#!/usr/bin/env python3
"""
cloud_duplicate_analyzer.py
────────────────────────────────────────────────────────────────────────────
Scans two or more directories and produces an HTML report describing:

  • How many files each directory contains
  • Which files are duplicated across directories (by name+size, name+mtime,
    or MD5 checksum for ambiguous cases)
  • Whether duplicate copies have diverged (different modification dates)
  • How folder sub-trees relate: identical / subset / superset / overlap

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
    --no-checksum          Skip MD5 checksums; rely only on name+size+mtime.
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
    """Return a list of file records with relative path, name, size, mtime."""
    records = []
    for dirpath, dirnames, filenames in os.walk(root):
        if skip_hidden:
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            filenames = [f for f in filenames if not f.startswith(".")]
        for fname in filenames:
            if fname == ".DS_Store":
                continue
            full = Path(dirpath) / fname
            try:
                st = full.stat()
                size = st.st_size
                mtime = st.st_mtime
            except (OSError, PermissionError):
                size, mtime = 0, 0.0
            rel = full.relative_to(root)
            records.append({
                "rel_path": str(rel),
                "name": fname.lower(),           # lower for case-insensitive match
                "name_orig": fname,
                "size": size,
                "mtime": mtime,
                "full_path": full,
                "folder": str(rel.parent),
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

      content_match : 'identical' | 'different' | 'unverified'
      version_status: 'same'      | 'diverged'  | 'phantom'

    'phantom' means mtime agrees but MD5 differs — the most dangerous case:
    the file looks like a safe duplicate but the content is actually different.

    Empty files (size == 0) are always classified ("identical", "same") regardless
    of mtime. There is no content to version, so mtime differences on empty files
    are always sync artifacts rather than meaningful edits.
    """
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

    # ── find duplicate groups ──────────────────────────────────────
    # A group = same logical file appearing in 2+ directories.
    # Key: (normalised_name, size_bucket) — we match pairwise then cluster.

    print("\nMatching files across directories …")

    # Map: rel_path_lower -> list of (label, record)
    all_keys = set()
    for label, recs in scanned.items():
        for r in recs:
            all_keys.add((r["name"], r["size"]))

    duplicate_groups = []   # each: {"rel_path", "matches": {label: record}, "confidence"}
    seen_keys = set()

    for key in all_keys:
        name, size = key
        present_in = {}
        for label in labels:
            hits = indexes[label].get(key, [])
            if hits:
                present_in[label] = hits[0]   # take first if multiple (unusual)
        if len(present_in) < 2:
            continue
        # Verify pairwise
        label_list = list(present_in.keys())
        confirmed = {}
        confidence = "exact"
        for la, lb in combinations(label_list, 2):
            c = classify_pair(present_in[la], present_in[lb], mtime_fuzz, use_checksum)
            if c is None or c[0] == "different":
                break
            if c[0] == "unverified":
                confidence = "likely"
        else:
            for label in present_in:
                confirmed[label] = present_in[label]

        if len(confirmed) >= 2:
            # Rel path — use the one from the first label that has it
            rel = present_in[label_list[0]]["rel_path"]
            duplicate_groups.append({
                "rel_path": rel,
                "name_orig": present_in[label_list[0]]["name_orig"],
                "size": size,
                "matches": confirmed,
                "confidence": confidence,
            })

    # ── version divergence ────────────────────────────────────────
    for g in duplicate_groups:
        mtimes = {label: rec["mtime"] for label, rec in g["matches"].items()}
        max_mt = max(mtimes.values())
        min_mt = min(mtimes.values())
        diff_days = (max_mt - min_mt) / 86400
        if diff_days * 86400 > mtime_fuzz:
            newest_label = max(mtimes, key=mtimes.get)
            g["version_status"] = "diverged"
            g["newest_in"] = newest_label
            g["newest_mtime"] = max_mt
            g["age_difference_days"] = round(diff_days, 2)
            g["copy_mtimes"] = {l: fmt_ts(t) for l, t in mtimes.items()}
        else:
            g["version_status"] = "same"
            g["newest_in"] = None
            g["age_difference_days"] = 0.0
            g["copy_mtimes"] = {l: fmt_ts(t) for l, t in mtimes.items()}

    # ── pairwise duplicate counts ─────────────────────────────────
    pairwise_counts = {}
    for la, lb in combinations(labels, 2):
        count = sum(1 for g in duplicate_groups if la in g["matches"] and lb in g["matches"])
        pairwise_counts[(la, lb)] = count
    all_three_count = sum(1 for g in duplicate_groups if len(g["matches"]) == n)

    # ── unique files per service ──────────────────────────────────
    dup_rel_paths = defaultdict(set)
    for g in duplicate_groups:
        for label in g["matches"]:
            dup_rel_paths[label].add(g["rel_path"].lower())

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

    folder_comparisons = []
    for folder in sorted(all_folders):
        present = [l for l in labels if folder in folder_sets[l]]
        if len(present) < 2:
            continue
        sets_here = {l: folder_sets[l][folder] for l in present}

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
            in_pair = sets_here[la] & sets_here[lb] - in_all
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

    return {
        "labels": labels,
        "dirs": {label: str(path) for label, path in dirs},
        "total_files": {label: len(recs) for label, recs in scanned.items()},
        "duplicate_groups": duplicate_groups,
        "unique_counts": unique_counts,
        "pairwise_counts": {f"{la}↔{lb}": v for (la, lb), v in pairwise_counts.items()},
        "all_services_count": all_three_count,
        "folder_comparisons": folder_comparisons,
        "relationship_counts": dict(rel_counts),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


# ─────────────────────────────────────────────────────── HTML report ──

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
.badge-overlap    { background:#fff3cd; color:#856404; }
.badge-subset     { background:#d1ecf1; color:#0c5460; }
.badge-superset   { background:#d1ecf1; color:#0c5460; }
.badge-diverged   { background:#f8d7da; color:#721c24; }
.badge-same       { background:#d4edda; color:#155724; }
.badge-exact      { background:#d4edda; color:#155724; }
.badge-likely     { background:#fff3cd; color:#856404; }
.warn-row td      { background:#fff8e1 !important; }
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
                 f"MD5 checksums were computed for ambiguous cases.</p>")

    # ── section 3: duplicate file list ──
    parts.append(f"<h2>3. Duplicate Files ({len(dups)} confirmed)</h2>")
    if not dups:
        parts.append("<p>No duplicate files found.</p>")
    else:
        parts.append('<table><tr><th>File</th><th>Folder</th><th>Size</th>'
                     '<th>Found in</th><th>Match</th><th>Version</th></tr>')
        for g in sorted(dups, key=lambda x: x["rel_path"]):
            row_cls = ' class="warn-row"' if g["version_status"] == "diverged" else ""
            found_in = ", ".join(g["matches"].keys())
            version_cell = badge("diverged") if g["version_status"] == "diverged" else badge("same")
            match_cell = badge(g["confidence"])
            # folder = parent of rel_path
            rp = Path(g["rel_path"])
            folder_str = str(rp.parent) if str(rp.parent) != "." else "(root)"
            parts.append(f'<tr{row_cls}>'
                         f'<td>{html.escape(g["name_orig"])}</td>'
                         f'<td><code>{html.escape(folder_str)}</code></td>'
                         f'<td style="white-space:nowrap">{human_size(g["size"])}</td>'
                         f'<td>{html.escape(found_in)}</td>'
                         f'<td>{match_cell}</td>'
                         f'<td>{version_cell}</td></tr>')
        parts.append("</table>")

    # ── section 4: version diverged ──
    parts.append(f"<h2>4. Version-Diverged Files ({len(divs)} files — action required)</h2>")
    if not divs:
        parts.append("<p>All duplicate files have identical modification timestamps — no action needed.</p>")
    else:
        parts.append("<p>These files have copies with different modification dates across services. "
                     "Review them to decide which version to keep before removing duplicates.</p>")
        parts.append('<table><tr><th>File</th><th>Path</th><th>Newest version in</th>'
                     '<th>Age gap (days)</th><th>Copy dates</th></tr>')
        for g in sorted(divs, key=lambda x: -x["age_difference_days"]):
            rp = Path(g["rel_path"])
            folder_str = str(rp.parent) if str(rp.parent) != "." else "(root)"
            copy_dates = "<br>".join(
                f'{html.escape(l)}: {t}' for l, t in g["copy_mtimes"].items())
            parts.append(f'<tr class="warn-row">'
                         f'<td><strong>{html.escape(g["name_orig"])}</strong></td>'
                         f'<td><code>{html.escape(folder_str)}</code></td>'
                         f'<td><strong>{html.escape(g["newest_in"])}</strong></td>'
                         f'<td>{g["age_difference_days"]:.1f}</td>'
                         f'<td style="font-size:11px">{copy_dates}</td></tr>')
        parts.append("</table>")

    # ── section 5: folder comparisons ──
    fc_list = result["folder_comparisons"]
    rcounts = result["relationship_counts"]
    parts.append(f"<h2>5. Folder Structure Analysis ({len(fc_list)} shared folders)</h2>")
    parts.append('<div class="stat-grid">')
    for rel, cnt in sorted(rcounts.items()):
        parts.append(f'<div class="stat-card"><div class="num">{cnt}</div>'
                     f'<div class="lbl">{badge(rel)} folders</div></div>')
    parts.append("</div>")

    # Group by relationship
    for rel_type in ["identical", "subset/superset", "overlap"]:
        subset = [fc for fc in fc_list if fc["relationship"] == rel_type]
        if not subset:
            continue
        label_str = rel_type.capitalize()
        parts.append(f"<h3>{label_str} folders ({len(subset)})</h3>")

        if rel_type == "identical":
            parts.append('<table><tr><th>Folder</th><th>Services</th><th>Files</th></tr>')
            for fc in sorted(subset, key=lambda x: x["folder_path"]):
                svc = ", ".join(fc["services_present"])
                parts.append(f'<tr><td><code>{html.escape(fc["folder_path"])}</code></td>'
                             f'<td>{html.escape(svc)}</td>'
                             f'<td>{fc["files_in_all"]}</td></tr>')
            parts.append("</table>")

        elif rel_type == "subset/superset":
            parts.append('<table><tr><th>Folder</th><th>Services</th><th>Relationship</th></tr>')
            for fc in sorted(subset, key=lambda x: x["folder_path"]):
                svc = ", ".join(fc["services_present"])
                d = fc["details"]
                only_lines = []
                for label in fc["services_present"]:
                    onlys = d.get(f"{label}_only", [])
                    if onlys:
                        only_lines.append(f'{label} has {len(onlys)} extra file(s): '
                                          + ", ".join(onlys[:5])
                                          + ("…" if len(onlys) > 5 else ""))
                rel_desc = "; ".join(only_lines) if only_lines else "—"
                parts.append(f'<tr><td><code>{html.escape(fc["folder_path"])}</code></td>'
                             f'<td>{html.escape(svc)}</td>'
                             f'<td>{html.escape(rel_desc)}</td></tr>')
            parts.append("</table>")

        else:  # overlap
            parts.append('<table><tr><th>Folder</th><th>In all</th>'
                         + "".join(f'<th>{html.escape(l)} only</th>' for l in labels)
                         + '</tr>')
            for fc in sorted(subset, key=lambda x: x["folder_path"]):
                d = fc["details"]
                in_all = len(d.get("in_all", []))

                def fmt_only(lbl):
                    items = d.get(f"{lbl}_only", [])
                    if not items:
                        return '<span style="color:#aaa">—</span>'
                    preview = ", ".join(items[:3])
                    if len(items) > 3:
                        preview += f' (+{len(items)-3} more)'
                    return html.escape(preview)

                only_cells = "".join(f'<td style="font-size:12px">{fmt_only(l)}</td>'
                                     for l in labels)
                parts.append(f'<tr><td><code>{html.escape(fc["folder_path"])}</code></td>'
                             f'<td>{in_all}</td>{only_cells}</tr>')
            parts.append("</table>")

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
                   help="Disable MD5 checksum verification")
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
        # Remove full_path (Path objects) before serialising
        clean = json.loads(json.dumps(result, default=str))
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
    divs = [g for g in dups if g["version_status"] == "diverged"]
    if divs:
        print(f"\n  ⚠  {len(divs)} file(s) have diverged versions — see Section 4 of report")
    rc = result["relationship_counts"]
    print(f"\n  Folder relationships:")
    for rel, cnt in sorted(rc.items()):
        print(f"    {rel:20s}: {cnt}")


if __name__ == "__main__":
    main()
