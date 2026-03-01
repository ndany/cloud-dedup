# Issue #1: Section Reordering + Duplicate Summary Improvements

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix section render order in `render_html()`, swap Section 3 subsections, improve the Duplicate Summary table with color-coded match/version breakdowns, and add a Version-Diverged Files subsection to Section 5.

**Architecture:** All changes are in `render_html()` (~line 786–1183 of `src/cloud_duplicate_analyzer.py`). `analyze()` is untouched — tests cover analyze logic, not rendering. Commits are kept small: reorder first, then improve, then new subsection, then docs.

**Tech Stack:** Python 3.8+, stdlib only, `html.escape()` for all user-controlled strings.

---

## Context

### Current render order (wrong)
```
Section 1: File Counts           ✓ correct position
Section 2: Duplicate Summary     ✓ correct position (but needs improvement)
  → h2: "5. Duplicate Files"     ✗ renders third, wrong
  → Symlinks subsection          ✗ renders fourth
  → h2: "4. Files Requiring..."  ✗ renders fifth
  → h2: "3. Folder Structure..." ✗ renders last (and subsections reversed)
Footer
```

### Target render order (correct)
```
Section 1: File Counts
Section 2: Duplicate Summary     (improved — new Match/Version columns)
Section 3: Folder Structure      (moved earlier; Folder Tree first, Safe to Delete second)
Section 4: Files Requiring Action
Section 5: Duplicate Files       (moved to end; + new Version-Diverged subsection)
Footer
```

### Key line ranges in render_html() (current)

| Block | Lines |
|---|---|
| Function header + init vars | 786–791 |
| Section 1 (File Counts) | 793–819 |
| Section 2 (Duplicate Summary) | 821–838 |
| Section 5 table (Duplicate Files) | 840–860 |
| Section 5 Symlinks subsection | 862–885 |
| Section 4 (Files Requiring Action) | 887–1000 |
| Section 3 header + Part 1 (Safe to Delete) | 1002–1039 |
| Section 3 Part 2 (Folder Tree) incl. render_node() | 1041–1176 |
| Footer | 1178–1183 |

---

## Task 1: Reorder sections in render_html()

**Files:**
- Modify: `src/cloud_duplicate_analyzer.py:840–1176`

No new tests. `render_html()` is not tested. After reordering, run all tests to confirm `analyze()` still passes.

### Step 1: Move section blocks

The change: restructure the code inside `render_html()` so blocks execute in this order:
1. Init vars (lines 787–791) — unchanged
2. Section 1 (lines 793–819) — unchanged
3. Section 2 (lines 821–838) — unchanged (improved in Task 2)
4. **Section 3** — moved up from line 1002, subsections swapped
5. **Section 4** — stays logically the same, now renders fourth
6. **Section 5** — moved to end, now renders fifth

**Exact rewrite:** Remove lines 840–1176 and replace with the blocks in this new order:

```python
    # ── section 3: folder structure analysis ──
    fc_list        = result["folder_comparisons"]
    safe_roots     = result.get("safe_to_delete_roots", [])
    file_cls       = result.get("_file_classifications", {})
    scanned_recs   = result.get("_scanned_records", {})

    parts.append(f"<h2>3. Folder Structure Analysis ({len(fc_list)} shared folders)</h2>")

    # ── part 1: folder tree ── (was Part 2, now first)
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

    # ── part 2: actionability panel ── (was Part 1, now second)
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

    # Version-Diverged Files subsection — placeholder for Task 3
    # (added in Task 3)
```

### Step 2: Run tests

```bash
python3 -m pytest tests/ -v
```

Expected: all tests pass (tests only cover analyze(), not render_html()).

### Step 3: Commit

```bash
git add src/cloud_duplicate_analyzer.py
git commit -m "$(cat <<'EOF'
refactor: reorder report sections to match spec (1→2→3→4→5)

Move Folder Structure Analysis before Files Requiring Action,
move Duplicate Files to last. Swap Section 3 subsections so
Folder Tree appears before Safe-to-Delete panel.

Closes part of #1.
EOF
)"
```

---

## Task 2: Improve Duplicate Summary (Section 2)

**Files:**
- Modify: `src/cloud_duplicate_analyzer.py:821–838` (Section 2 block)

No new tests needed.

### Step 1: Compute per-pair breakdown

Replace lines 821–838 with:

```python
    # ── section 2: duplicate summary ──
    parts.append("<h2>2. Duplicate File Summary</h2>")

    # Compute per-pair match/version breakdowns
    pair_stats: dict = {}
    for i, la in enumerate(labels):
        for lb in labels[i + 1:]:
            pair_key = f"{la}↔{lb}"
            dup_in_pair = [
                g for g in dups
                if la in g["matches"] and lb in g["matches"]
            ]
            conf_in_pair = [
                g for g in result.get("conflict_groups", [])
                if la in g.get("service_details", {}) and lb in g.get("service_details", {})
            ]
            all_in_pair = dup_in_pair + conf_in_pair
            pair_stats[pair_key] = {
                "identical":  sum(1 for g in dup_in_pair  if g["content_match"] == "identical"),
                "unverified": sum(1 for g in dup_in_pair  if g["content_match"] == "unverified"),
                "different":  len(conf_in_pair),
                "same":       sum(1 for g in all_in_pair  if g["version_status"] == "same"),
                "diverged":   sum(1 for g in all_in_pair  if g["version_status"] == "diverged"),
                "phantom":    sum(1 for g in all_in_pair  if g["version_status"] == "phantom"),
                "total":      len(all_in_pair),
            }
    if n > 2:
        all_svc_total = result["all_services_count"]

    parts.append(
        '<table>'
        '<tr><th>Service Pair</th><th>Match Type</th><th>Version Status</th><th>Total</th></tr>'
    )
    for pair_key, ps in pair_stats.items():
        match_parts = []
        if ps["identical"]:
            match_parts.append(
                f'<span style="color:#28a745;font-weight:bold">{ps["identical"]:,} identical</span>'
            )
        if ps["unverified"]:
            match_parts.append(
                f'<span style="color:#888">{ps["unverified"]:,} unverified</span>'
            )
        if ps["different"]:
            match_parts.append(
                f'<span style="color:#dc3545;font-weight:bold">{ps["different"]:,} different</span>'
            )
        if not match_parts:
            match_parts.append('<span style="color:#aaa">—</span>')

        version_parts = []
        if ps["diverged"]:
            version_parts.append(
                f'<span style="color:#0069c0">{ps["diverged"]:,} diverged</span>'
            )
        if ps["phantom"]:
            version_parts.append(
                f'<span style="color:#dc3545">{ps["phantom"]:,} phantom</span>'
            )
        if ps["same"]:
            version_parts.append(
                f'<span style="color:#888">{ps["same"]:,} same</span>'
            )
        if not version_parts:
            version_parts.append('<span style="color:#aaa">—</span>')

        parts.append(
            f'<tr>'
            f'<td>{html.escape(pair_key)}</td>'
            f'<td>{" &nbsp;|&nbsp; ".join(match_parts)}</td>'
            f'<td>{" &nbsp;|&nbsp; ".join(version_parts)}</td>'
            f'<td>{ps["total"]:,}</td>'
            f'</tr>'
        )

    if n > 2:
        parts.append(
            f'<tr><td><strong>All {n} services</strong></td>'
            f'<td colspan="2"><em>(pairwise breakdown only)</em></td>'
            f'<td><strong>{all_svc_total:,}</strong></td></tr>'
        )

    unique_str = " &nbsp;|&nbsp; ".join(
        f'{html.escape(l)}: {result["unique_counts"][l]:,} unique' for l in labels
    )
    parts.append(f'<tr><td colspan="4"><em>{unique_str}</em></td></tr>')
    parts.append("</table>")
    parts.append(
        f"<p>Duplicate matching used: same filename + same size. "
        f"MD5 checksums were computed for all candidate pairs "
        f"(mtime tolerance: {result.get('mtime_fuzz', 5)}s).</p>"
    )
```

### Step 2: Run tests

```bash
python3 -m pytest tests/ -v
```

Expected: all tests pass.

### Step 3: Commit

```bash
git add src/cloud_duplicate_analyzer.py
git commit -m "$(cat <<'EOF'
feat: replace Duplicate Summary table with match/version breakdown

Show per-pair counts of identical/different/unverified (Match Type)
and same/diverged/phantom (Version Status) with color coding.

Part of #1.
EOF
)"
```

---

## Task 3: Add Version-Diverged Files subsection to Section 5

**Files:**
- Modify: `src/cloud_duplicate_analyzer.py` — add after Symlinks subsection inside Section 5 block

### Step 1: Update the `divs` variable filter and add the subsection

At line 790, update `divs` to be more explicit (currently unused):
```python
    divs = [g for g in dups if g["version_status"] == "diverged"
            and g["content_match"] in ("identical", "unverified")]
```

Then add this block directly after the Symlinks subsection (after `parts.append('</table>')`):

```python
    # Version-Diverged Files subsection
    if divs:
        parts.append(f'<h3>Version-Diverged Files ({len(divs)})</h3>')
        parts.append(
            '<p>These files have identical content across services but different modification '
            'timestamps (beyond the mtime tolerance). The copy with the newest timestamp is '
            'shown. Safe to delete older copies — content is confirmed identical.</p>'
        )
        parts.append(
            '<table><tr><th>File</th><th>Folder</th><th>Size</th>'
            '<th>Found in</th><th>Newest in</th></tr>'
        )
        for g in sorted(divs, key=lambda x: x["rel_path"]):
            found_in = ", ".join(g["matches"].keys())
            rp = Path(g["rel_path"])
            folder_str = str(rp.parent) if str(rp.parent) != "." else "(root)"
            newest = html.escape(g.get("newest_in") or "—")
            parts.append(
                f'<tr>'
                f'<td>{html.escape(g["name_orig"])}</td>'
                f'<td><code>{html.escape(folder_str)}</code></td>'
                f'<td style="white-space:nowrap">{human_size(g["size"])}</td>'
                f'<td>{html.escape(found_in)}</td>'
                f'<td><strong>{newest}</strong></td>'
                f'</tr>'
            )
        parts.append('</table>')
```

### Step 2: Run tests

```bash
python3 -m pytest tests/ -v
```

Expected: all tests pass.

### Step 3: Commit

```bash
git add src/cloud_duplicate_analyzer.py
git commit -m "$(cat <<'EOF'
feat: add Version-Diverged Files subsection to Section 5

Shows identical-content files with mismatched timestamps separately,
calling out which service has the newest copy.

Part of #1.
EOF
)"
```

---

## Task 4: Update documentation

**Files:**
- Modify: `docs/report-format.md`
- Modify: `README.md`

### Step 1: Update docs/report-format.md

Replace the **Section 2** description (currently line 29–33):

```markdown
### Section 2: Duplicate File Summary

A table showing per-pair match and version breakdowns across all compared service pairs:

| Column | Description |
|---|---|
| Service Pair | The two services being compared (e.g. `Google Drive↔Dropbox`) |
| Match Type | Color-coded counts: **identical** (green), **different** (red), **unverified** (gray) |
| Version Status | Color-coded counts: **diverged** (blue), **phantom** (red), **same** (gray) |
| Total | All files shared between the pair (duplicates + conflicts) |

Also shows per-service unique file counts (files not duplicated anywhere).
```

Replace the **Section 3** description to swap subsection order (currently line 34–40):

```markdown
### Section 3: Folder Structure Analysis

Two parts:

- **Part 1 — Folder tree**: collapsible `<details>`/`<summary>` nodes. Each node shows the subtree status symbol (★ = identical subtree, ~ = partially duplicated, ✗ = has conflicts), per-folder file counts, and file-level detail within each expanded folder. Files shared across services are listed under "Shared across services" and annotated with ★/✓/⚠/⚡ per their match status; ⚠ and ⚡ files link to Section 4. Files unique to one service are listed under "Only in &lt;service&gt;" with a ◆ marker.

- **Part 2 — Fully duplicated subtrees** panel: a table listing each `safe_to_delete_roots` entry with a per-service ✓ or — column and a total file count for the subtree. Only shown when at least one fully-identical subtree exists.
```

Replace the **Section 5** description to add Version-Diverged subsection (currently line 59–73):

```markdown
### Section 5: Duplicate Files

Three subsections:

**Duplicate Files** — A row per confirmed duplicate group (files with `content_match = identical` or `unverified`). Columns:

| Column | Description |
|---|---|
| File | Filename |
| Folder | Relative folder path within the directory |
| Size | Human-readable file size |
| Found in | Which services contain this file |
| Match | Combined `content_match · version_status` badge, e.g. `identical · same`, `identical · diverged`, `unverified · same` |

**Symlinks** — A row per symlink pair where both services agree on the resolved target. Each row shows the symlink name, relative folder, the resolved target path, and which services contain it. Annotated with the ↪ symbol. Dangling symlinks (no resolved target) are shown with a `—` in the Target column.

**Version-Diverged Files** — Files where `content_match = identical` (or `unverified`) but `version_status = diverged`. Content is confirmed identical; only the modification timestamp differs beyond the tolerance window. Columns: File, Folder, Size, Found in, Newest in (which service has the latest copy). Safe to delete from any service — content matches.
```

### Step 2: Update README.md

Replace lines 114–122 (the five-section list):

```markdown
The HTML report has five sections:

1. **File Counts** — how many files are in each directory
2. **Duplicate File Summary** — per-pair counts of identical/different/unverified files, with version status (same/diverged/phantom) breakdown
3. **Folder Structure Analysis** — collapsible folder tree with per-folder file status, plus a safe-to-delete subtree panel
4. **Files Requiring Action** — files with different content across services that need manual review before deletion
5. **Duplicate Files** — confirmed duplicates with size, match status, version status; includes symlinks and version-diverged files subsections
```

### Step 3: Run tests

```bash
python3 -m pytest tests/ -v
```

Expected: all tests pass (docs changes don't affect tests).

### Step 4: Commit

```bash
git add docs/report-format.md README.md
git commit -m "$(cat <<'EOF'
docs: update section descriptions for issue #1 restructure

Update report-format.md and README.md to reflect new section order
(1→2→3→4→5), new Duplicate Summary columns, swapped Section 3
subsections, and new Version-Diverged Files subsection in Section 5.

Closes #1.
EOF
)"
```

---

## Final Verification

Run the full test suite and a manual smoke test:

```bash
# All tests
python3 -m pytest tests/ -v

# Smoke test — generate a real report
python3 src/cloud_duplicate_analyzer.py "A:/tmp/a" "B:/tmp/b" -o /tmp/smoke_test.html
# Open /tmp/smoke_test.html in browser and verify:
# - Sections appear in order: 1 File Counts, 2 Duplicate Summary, 3 Folder Structure, 4 Files Requiring Action, 5 Duplicate Files
# - Section 2 shows Match Type + Version Status columns with color coding
# - Section 3 shows Folder Tree first, Safe to Delete second
# - Section 5 ends with Version-Diverged Files subsection (if any exist)
```
