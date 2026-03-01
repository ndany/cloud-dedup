# Symlink Detection and Handling Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans or superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Detect and explicitly report symlinks instead of silently following them; compare symlinks by target path rather than by reading their content.

**Architecture:**
During file scanning, detect symlinks explicitly using `Path.is_symlink()` and store target information. In the matching phase, compare symlinks by target path instead of following them. In analysis, symlinks are handled separately from regular files. In rendering, symlinks are displayed with `↪` symbol and target information. Update `◆` symbol for unique files (replacing `→`).

**Tech Stack:** Python 3.8+, stdlib only, existing Path/os.walk infrastructure.

**Symbols:**
- `★` = Identical file/folder
- `✓` = Identical · diverged
- `⚠` = Different · diverged
- `⚡` = Different · phantom
- `◆` = Unique to one service (changed from `→`)
- `↪` = Symlink (new)

---

## Task 1: Detect Symlinks During Directory Scan

**Files:**
- Modify: `src/cloud_duplicate_analyzer.py:125-152` (scan_directory function)
- Test: `tests/test_cloud_duplicate_analyzer.py` (new test)

**Step 1: Write the failing test for symlink detection**

Add to `tests/test_cloud_duplicate_analyzer.py`:

```python
def test_symlink_detection(self):
    """Verify symlinks are detected and their targets are stored."""
    # Create a regular file
    regular_file = make_file(self.tmp, "a/regular.txt", b"content")

    # Create a symlink pointing to it
    symlink_path = Path(self.tmp) / "b" / "link.txt"
    symlink_path.parent.mkdir(parents=True, exist_ok=True)
    symlink_path.symlink_to(regular_file)

    # Scan directory with symlink
    records = cda.scan_directory(Path(self.tmp) / "b", skip_hidden=False)

    # Find the symlink record
    symlink_record = next((r for r in records if r["name_orig"] == "link.txt"), None)

    # Verify symlink is detected and target is stored
    assert symlink_record is not None
    assert symlink_record.get("is_symlink") == True
    assert symlink_record.get("symlink_target") is not None
    assert str(regular_file) in str(symlink_record.get("symlink_target"))
```

**Step 2: Run test to verify it fails**

```bash
cd /Users/nissim/dev/cloud-dedup
python -m pytest tests/test_cloud_duplicate_analyzer.py::TestSymlinkDetection::test_symlink_detection -v
```

Expected output: `FAILED` - KeyError or AttributeError on `is_symlink` or `symlink_target`

**Step 3: Implement symlink detection in scan_directory()**

Modify `src/cloud_duplicate_analyzer.py` lines 125-152:

```python
def scan_directory(root: Path, skip_hidden: bool) -> list[dict]:
    """Return a list of file records with relative path, name, size, mtime.

    Detects symlinks explicitly. Symlink records have:
    - is_symlink: True
    - symlink_target: resolved path of target
    Regular file records have is_symlink: False (or omitted).
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

            # Detect symlinks explicitly (don't follow them with stat)
            is_symlink = full.is_symlink()

            if is_symlink:
                # For symlinks, get target path
                try:
                    target = full.resolve()
                    symlink_target = str(target)
                except (OSError, RuntimeError):
                    symlink_target = None

                rel = full.relative_to(root)
                records.append({
                    "rel_path": str(rel),
                    "name": fname.lower(),
                    "name_orig": fname,
                    "size": 0,  # Symlinks have no meaningful size
                    "mtime": 0.0,  # Symlinks have no meaningful mtime
                    "full_path": full,
                    "folder": str(rel.parent),
                    "is_symlink": True,
                    "symlink_target": symlink_target,
                })
            else:
                # Regular file: get metadata
                try:
                    st = full.stat()
                    size = st.st_size
                    mtime = st.st_mtime
                except (OSError, PermissionError):
                    size, mtime = 0, 0.0

                rel = full.relative_to(root)
                records.append({
                    "rel_path": str(rel),
                    "name": fname.lower(),
                    "name_orig": fname,
                    "size": size,
                    "mtime": mtime,
                    "full_path": full,
                    "folder": str(rel.parent),
                    "is_symlink": False,
                })
    return records
```

**Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_cloud_duplicate_analyzer.py::TestSymlinkDetection::test_symlink_detection -v
```

Expected output: `PASSED`

**Step 5: Commit**

```bash
git add src/cloud_duplicate_analyzer.py tests/test_cloud_duplicate_analyzer.py
git commit -m "feat: detect symlinks during directory scan with target tracking"
```

---

## Task 2: Handle Symlinks in File Matching Logic

**Files:**
- Modify: `src/cloud_duplicate_analyzer.py` - Add new symlink comparison function
- Test: `tests/test_cloud_duplicate_analyzer.py` (new tests)

**Step 1: Write tests for symlink comparison**

Add to `tests/test_cloud_duplicate_analyzer.py`:

```python
def test_symlink_identical_targets(self):
    """Two symlinks pointing to same target are identical."""
    target = make_file(self.tmp, "target/file.txt", b"target content")
    link1 = Path(self.tmp) / "a" / "link.txt"
    link2 = Path(self.tmp) / "b" / "link.txt"

    link1.parent.mkdir(parents=True, exist_ok=True)
    link2.parent.mkdir(parents=True, exist_ok=True)
    link1.symlink_to(target)
    link2.symlink_to(target)

    rec_a = {
        "name": "link.txt", "name_orig": "link.txt",
        "is_symlink": True, "symlink_target": str(target),
        "folder": "a"
    }
    rec_b = {
        "name": "link.txt", "name_orig": "link.txt",
        "is_symlink": True, "symlink_target": str(target),
        "folder": "b"
    }

    result = cda.classify_pair(rec_a, rec_b, mtime_fuzz=5.0, use_checksum=True)
    # Symlinks with identical targets should compare equal
    self.assertEqual(result, ("symlink", "target_identical"))

def test_symlink_diverged_targets(self):
    """Two symlinks pointing to different targets diverge."""
    target1 = Path(self.tmp) / "target1.txt"
    target2 = Path(self.tmp) / "target2.txt"
    target1.write_text("1")
    target2.write_text("2")

    rec_a = {
        "name": "link.txt", "name_orig": "link.txt",
        "is_symlink": True, "symlink_target": str(target1),
        "folder": "a"
    }
    rec_b = {
        "name": "link.txt", "name_orig": "link.txt",
        "is_symlink": True, "symlink_target": str(target2),
        "folder": "b"
    }

    result = cda.classify_pair(rec_a, rec_b, mtime_fuzz=5.0, use_checksum=True)
    self.assertEqual(result, ("symlink", "target_diverged"))

def test_symlink_vs_file_conflict(self):
    """Symlink in one service, regular file in another = conflict."""
    target = Path(self.tmp) / "target.txt"
    target.write_text("target")

    rec_symlink = {
        "name": "item.txt", "name_orig": "item.txt",
        "is_symlink": True, "symlink_target": str(target),
        "folder": "a"
    }
    rec_file = {
        "name": "item.txt", "name_orig": "item.txt",
        "is_symlink": False, "size": 100, "mtime": 1000.0,
        "folder": "b"
    }

    result = cda.classify_pair(rec_symlink, rec_file, mtime_fuzz=5.0, use_checksum=True)
    self.assertEqual(result, ("mixed_type", "conflict"))
```

**Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_cloud_duplicate_analyzer.py::TestSymlinkDetection -v -k "identical_targets or diverged_targets or vs_file"
```

Expected output: Tests fail (classify_pair doesn't handle symlinks)

**Step 3: Extend classify_pair() to handle symlinks**

Modify `src/cloud_duplicate_analyzer.py` `classify_pair()` function (around line 164):

```python
def classify_pair(a: dict, b: dict, mtime_fuzz: float, use_checksum: bool):
    """
    Compare two file records that share the same (name, size) index key.

    Returns (content_match, version_status) or None if name/size don't match.

    For symlinks, compares by target path, not by content.
    Mixed file+symlink pairs return ("mixed_type", "conflict").
    """
    if a["name"] != b["name"]:
        return None

    # Check if either is a symlink
    a_is_symlink = a.get("is_symlink", False)
    b_is_symlink = b.get("is_symlink", False)

    # Mixed file + symlink = conflict
    if a_is_symlink != b_is_symlink:
        return ("mixed_type", "conflict")

    # Both are symlinks: compare by target
    if a_is_symlink and b_is_symlink:
        a_target = a.get("symlink_target")
        b_target = b.get("symlink_target")

        if a_target == b_target:
            return ("symlink", "target_identical")
        else:
            return ("symlink", "target_diverged")

    # Both are regular files: existing logic
    if a["size"] != b["size"]:
        return None

    mtime_same = abs(a["mtime"] - b["mtime"]) <= mtime_fuzz

    # Empty files: no content to version
    if a["size"] == 0:
        return ("identical", "same")

    if not use_checksum:
        return ("unverified", "same" if mtime_same else "diverged")

    hash_a = md5(a["full_path"])
    hash_b = md5(b["full_path"])

    if not hash_a or not hash_b:
        return ("unverified", "same" if mtime_same else "diverged")

    if hash_a == hash_b:
        return ("identical", "same" if mtime_same else "diverged")
    else:
        return ("different", "phantom" if mtime_same else "diverged")
```

**Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_cloud_duplicate_analyzer.py::TestSymlinkDetection -v
```

Expected output: All tests `PASSED`

**Step 5: Commit**

```bash
git add src/cloud_duplicate_analyzer.py tests/test_cloud_duplicate_analyzer.py
git commit -m "feat: add symlink comparison by target path (not content)"
```

---

## Task 3: Update analyze() to Handle Symlinks Separately

**Files:**
- Modify: `src/cloud_duplicate_analyzer.py:208-320` (analyze function)
- Test: `tests/test_cloud_duplicate_analyzer.py` (integration test)

**Step 1: Write integration test for symlink analysis**

Add to `tests/test_cloud_duplicate_analyzer.py`:

```python
def test_symlink_analysis_integration(self):
    """Verify symlinks are analyzed separately in results."""
    import tempfile
    import shutil

    # Create two directories
    dir_a = Path(tempfile.mkdtemp())
    dir_b = Path(tempfile.mkdtemp())

    try:
        # Create regular files
        make_file(dir_a, "file.txt", b"content")
        make_file(dir_b, "file.txt", b"content")

        # Create symlinks pointing to external target
        target = dir_a / "target.txt"
        target.write_text("target")

        (dir_a / "link.txt").symlink_to(target)
        (dir_b / "link.txt").symlink_to(target)

        # Analyze
        result = cda.analyze(
            [("DirA", dir_a), ("DirB", dir_b)],
            mtime_fuzz=5.0,
            use_checksum=True,
            skip_hidden=True
        )

        # Verify result has symlinks section
        assert "symlinks" in result
        symlinks = result["symlinks"]

        # Verify link.txt is in symlinks, not duplicate_groups
        link_symlinks = [s for s in symlinks if s["name_orig"] == "link.txt"]
        assert len(link_symlinks) > 0

        # Verify symlink has target info
        link_sym = link_symlinks[0]
        assert link_sym["is_symlink"] == True
        assert link_sym["symlink_target"] is not None

    finally:
        shutil.rmtree(dir_a)
        shutil.rmtree(dir_b)
```

**Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_cloud_duplicate_analyzer.py::TestSymlinkAnalysis::test_symlink_analysis_integration -v
```

Expected output: `FAILED` - 'symlinks' key missing from result

**Step 3: Update analyze() to create symlinks section**

Modify `src/cloud_duplicate_analyzer.py` `analyze()` function to create a `symlinks` list similar to `duplicate_groups` and `conflict_groups`:

After line 238 where `duplicate_groups` and `conflict_groups` are initialized, add:

```python
    symlinks = []  # Symlinks with target information
```

In the matching loop (around line 241-260), update to separate symlinks:

```python
    for key in all_keys:
        name, size = key
        present_in = {}
        for label in labels:
            hits = indexes[label].get(key, [])
            if hits:
                present_in[label] = hits[0]
        if len(present_in) < 2:
            continue

        # Check if all matching records are symlinks
        is_all_symlinks = all(rec.get("is_symlink", False) for rec in present_in.values())

        if is_all_symlinks:
            # Handle symlinks separately
            first_rec = next(iter(present_in.values()))
            symlink_entry = {
                "name_orig": first_rec["name_orig"],
                "rel_path": first_rec["rel_path"],
                "is_symlink": True,
                "symlink_targets": {label: rec.get("symlink_target") for label, rec in present_in.items()},
                "matches": present_in,
                "symlink_status": "target_identical" if len(set(
                    rec.get("symlink_target") for rec in present_in.values()
                )) == 1 else "target_diverged"
            }
            symlinks.append(symlink_entry)
            continue

        # Check for mixed type (symlink + file)
        has_symlink = any(rec.get("is_symlink", False) for rec in present_in.values())
        has_file = any(not rec.get("is_symlink", False) for rec in present_in.values())

        if has_symlink and has_file:
            # Mixed type conflict
            conflict = {
                "name_orig": next(iter(present_in.values()))["name_orig"],
                "rel_path": next(iter(present_in.values()))["rel_path"],
                "content_match": "mixed_type",
                "version_status": "conflict",
                "service_details": {label: {
                    "size": rec["size"],
                    "mtime": fmt_ts(rec["mtime"]),
                    "is_symlink": rec.get("is_symlink", False),
                    "symlink_target": rec.get("symlink_target") if rec.get("is_symlink") else None
                } for label, rec in present_in.items()},
                "matches": present_in
            }
            conflict_groups.append(conflict)
            continue

        # Regular file matching logic (existing code)
        # ... rest of matching logic ...
```

**Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_cloud_duplicate_analyzer.py::TestSymlinkAnalysis::test_symlink_analysis_integration -v
```

Expected output: `PASSED`

**Step 5: Add symlinks to result dict**

After the matching loop completes, add symlinks to the result dict (around line 310):

```python
    result["symlinks"] = symlinks
```

**Step 6: Commit**

```bash
git add src/cloud_duplicate_analyzer.py tests/test_cloud_duplicate_analyzer.py
git commit -m "feat: separate symlink analysis with target tracking"
```

---

## Task 4: Update HTML Rendering - Folder Tree (Section 3)

**Files:**
- Modify: `src/cloud_duplicate_analyzer.py:795-890` (render_node function)

**Step 1: Update file symbol helper to include symlink symbol**

Modify `_file_sym()` function (around line 600):

```python
def _file_sym(content_match: str, version_status: str, is_symlink: bool = False):
    """Return symbol and CSS class for a file's match status."""
    if is_symlink:
        return ("↪", "sym-symlink")

    # Existing logic for regular files
    sym_map = {
        ("identical", "same"): ("★", "sym-is"),
        ("identical", "diverged"): ("✓", "sym-id"),
        ("different", "diverged"): ("⚠", "sym-dd"),
        ("different", "phantom"): ("⚡", "sym-dd"),
        ("unverified", "same"): ("★", "sym-is"),
        ("unverified", "diverged"): ("✓", "sym-id"),
    }
    return sym_map.get((content_match, version_status), ("?", ""))
```

**Step 2: Add CSS class for symlink symbol**

Modify CSS section (around line 450) to add:

```css
.sym-symlink {
    color: #0066cc;
    font-weight: bold;
}
```

**Step 3: Update folder tree rendering to display symlinks**

Modify `render_node()` function (around line 850) where files are displayed:

```python
            for fname, cls_info in in_multiple:
                if cls_info:
                    is_symlink = cls_info.get("is_symlink", False)
                    sym, sym_cls = _file_sym(
                        cls_info.get("content_match"),
                        cls_info.get("version_status"),
                        is_symlink=is_symlink
                    )

                    link = ""
                    if is_symlink:
                        target = cls_info.get("symlink_target")
                        link = f' <span style="font-size:11px;color:#888">{html.escape(target)}</span>'
                    elif cls_info.get("conflict_index") is not None:
                        link = (
                            f' <a href="#action-{cls_info["conflict_index"]}" '
                            f'style="font-size:10px;color:#888">&rarr;&nbsp;&sect;4</a>'
                        )

                    out.append(
                        f'<div class="tree-file">'
                        f'<span class="{sym_cls}">{sym}</span> '
                        f'{html.escape(fname)}{link}</div>'
                    )
```

**Step 4: Test HTML rendering manually**

After changes, run the tool on test data and inspect HTML output:

```bash
python3 src/cloud_duplicate_analyzer.py ~/test_dir1 ~/test_dir2 -o /tmp/test_report.html
```

Check that symlinks display with `↪` symbol and target paths.

**Step 5: Commit**

```bash
git add src/cloud_duplicate_analyzer.py
git commit -m "feat: display symlinks in folder tree with target information"
```

---

## Task 5: Update HTML Rendering - Section 5 (Duplicate Files List)

**Files:**
- Modify: `src/cloud_duplicate_analyzer.py:662-682` (Section 5 rendering)

**Step 1: Update duplicate files table to show symlinks**

Modify Section 5 rendering (around line 669):

```python
        for g in sorted(dups, key=lambda x: x["rel_path"]):
            # Skip symlinks (they're in symlinks section now)
            if g.get("is_symlink"):
                continue

            found_in = ", ".join(g["matches"].keys())
            match_label = f'{g.get("content_match", "unverified")} · {g.get("version_status", "same")}'
            match_cell  = badge(match_label, g.get("content_match", "unverified"))
            # ... rest of existing code ...
```

**Step 2: Add symlinks subsection to Section 5**

After the regular duplicates table, add symlinks display:

```python
    # Symlinks subsection
    symlinks_list = result.get("symlinks", [])
    if symlinks_list:
        parts.append(f"<h3>Symlinks ({len(symlinks_list)} found)</h3>")
        parts.append(
            '<table><tr><th>Symlink Name</th><th>Target</th><th>Status</th><th>Found in</th></tr>'
        )
        for sym in sorted(symlinks_list, key=lambda x: x["rel_path"]):
            target = sym.get("symlink_targets", {})
            status = sym.get("symlink_status", "unknown")
            found_in = ", ".join(sym.get("matches", {}).keys())

            status_badge = badge(f"symlink · {status}", "symlink")

            parts.append(
                f'<tr>'
                f'<td><strong>↪ {html.escape(sym["name_orig"])}</strong></td>'
                f'<td><code>{html.escape(str(target.get(next(iter(target)))))}</code></td>'
                f'<td>{status_badge}</td>'
                f'<td>{html.escape(found_in)}</td>'
                f'</tr>'
            )
        parts.append("</table>")
```

**Step 3: Update badge() function to handle symlink styling**

Modify `badge()` function (around line 605):

```python
def badge(text: str, cls: str) -> str:
    if cls not in ["identical", "different", "unverified", "symlink"]:
        cls = "unverified"
    return f'<span class="badge badge-{cls}">{html.escape(text)}</span>'
```

Add CSS for symlink badge (around line 450):

```css
.badge-symlink {
    background-color: #e6f2ff;
    color: #0066cc;
    border: 1px solid #0066cc;
}
```

**Step 4: Test manually**

Run tool again and check Section 5 for symlinks display.

**Step 5: Commit**

```bash
git add src/cloud_duplicate_analyzer.py
git commit -m "feat: add symlinks subsection to duplicate files list"
```

---

## Task 6: Update HTML Rendering - Section 4 (Files Requiring Action)

**Files:**
- Modify: `src/cloud_duplicate_analyzer.py:684-740` (Section 4 rendering)

**Step 1: Add symlink conflicts to Section 4**

Modify Section 4 to include symlink conflicts with diverged targets:

```python
    # Add symlink conflicts (target_diverged) to action items
    symlink_conflicts = [
        sym for sym in result.get("symlinks", [])
        if sym.get("symlink_status") == "target_diverged"
    ]

    # Also include mixed_type conflicts
    mixed_conflicts = [
        c for c in conflicts
        if c.get("content_match") == "mixed_type"
    ]

    all_action_items = conflicts + symlink_conflicts + mixed_conflicts

    parts.append(f"<h2>4. Files Requiring Action ({len(all_action_items)} items)</h2>")
```

Add rendering for symlink conflicts:

```python
    # Render symlink conflicts
    for sym in symlink_conflicts:
        parts.append(f"<h3>Symlink Conflict: {html.escape(sym['name_orig'])}</h3>")
        parts.append(
            '<table><tr><th>Service</th><th>Target</th></tr>'
        )
        for label, target in sym.get("symlink_targets", {}).items():
            parts.append(
                f'<tr><td>{html.escape(label)}</td>'
                f'<td><code>{html.escape(target)}</code></td></tr>'
            )
        parts.append("</table>")
        parts.append(
            '<p><strong>⚠ Symlinks point to different targets.</strong> '
            'Keep both unless you have consolidated the target locations.</p>'
        )
```

**Step 2: Test manually**

Create test data with diverged symlink targets and verify Section 4 display.

**Step 3: Commit**

```bash
git add src/cloud_duplicate_analyzer.py
git commit -m "feat: show symlink conflicts in Section 4"
```

---

## Task 7: Update JSON Output Schema

**Files:**
- Modify: `src/cloud_duplicate_analyzer.py:976-986` (JSON serialization)

**Step 1: Add symlinks to JSON output**

The JSON output should already include symlinks from the result dict. Verify at line 986:

```python
    # Add symlinks to JSON if they exist
    if "symlinks" in result:
        json_data["symlinks"] = result["symlinks"]
```

Verify the serialization properly handles symlink objects.

**Step 2: Test JSON output**

```bash
python3 src/cloud_duplicate_analyzer.py ~/test_dir1 ~/test_dir2 -o /tmp/test.json
python3 -m json.tool /tmp/test.json | head -50
```

Verify `symlinks` key exists and contains expected fields.

**Step 3: Commit**

```bash
git add src/cloud_duplicate_analyzer.py
git commit -m "feat: include symlinks in JSON output"
```

---

## Task 8: Update "Unique to One Service" Symbol from → to ◆

**Files:**
- Modify: `src/cloud_duplicate_analyzer.py` (multiple locations)

**Step 1: Update folder tree rendering**

Around line 880, change unique file symbol:

```python
                    for fname in ufiles:
                        out.append(
                            f'<div class="tree-file">'
                            f'<span class="sym-uniq">◆</span> '
                            f'{html.escape(fname)}</div>'
                        )
```

**Step 2: Add CSS for unique symbol**

Add to CSS section:

```css
.sym-uniq {
    color: #ff9900;
    font-weight: bold;
}
```

**Step 3: Update help text/documentation in docstring**

Update module docstring (line 10) to reflect new symbols.

**Step 4: Test manually**

Run tool and verify unique files display with `◆` symbol.

**Step 5: Commit**

```bash
git add src/cloud_duplicate_analyzer.py
git commit -m "feat: change 'unique to service' symbol from → to ◆"
```

---

## Task 9: Run Full Test Suite

**Files:**
- Test: `tests/test_cloud_duplicate_analyzer.py`

**Step 1: Run all tests**

```bash
python -m pytest tests/test_cloud_duplicate_analyzer.py -v
```

Expected: All tests pass (existing + new symlink tests)

**Step 2: Check test coverage**

```bash
python -m pytest tests/test_cloud_duplicate_analyzer.py --cov=src/cloud_duplicate_analyzer --cov-report=term-missing
```

Ensure symlink code paths have coverage.

**Step 3: Commit**

```bash
git add tests/
git commit -m "test: verify all symlink tests pass"
```

---

## Task 10: Update Documentation

**Files:**
- Modify: `docs/how-it-works.md`
- Modify: `docs/report-format.md`

**Step 1: Update how-it-works.md**

Add section after "Stage 2 — Content and Version Classification":

```markdown
### Symlink Handling

Symlinks are detected explicitly using `Path.is_symlink()` and are compared by their target path rather than by following them to their content. This ensures users know the scope of analysis.

- **Symlink Detection:** Every file scanned is checked for `is_symlink` property
- **Target Comparison:** Two symlinks are identical if they point to the same target path
- **Target Divergence:** If symlinks point to different targets, they are marked as diverged
- **Mixed Type Conflicts:** If one service has a regular file and another has a symlink with the same name, this is flagged as a conflict

Symlinks are reported separately from regular files in analysis output.
```

**Step 2: Update report-format.md**

Add to Section 3 description:

```
Symlinks are displayed with the ↪ symbol and include their target path. In the folder tree,
expanding a folder shows symlinks alongside regular files with their target information.
```

Update Section 5 to describe symlinks subsection.

Add new section describing symbol legend:

```markdown
### Symbol Legend

- `★` — Identical file/folder across all services
- `✓` — Identical content, different modification time
- `⚠` — Different content, different modification time
- `⚡` — Different content, same modification time (phantom)
- `◆` — Unique to one service
- `↪` — Symlink (compared by target, not content)
- `~` — Folder with partial duplication
- `✗` — Folder with conflicts
```

**Step 3: Commit**

```bash
git add docs/how-it-works.md docs/report-format.md
git commit -m "docs: update for symlink detection and new symbol ◆"
```

---

## Task 11: Integration Test - End-to-End

**Files:**
- Test: `tests/test_cloud_duplicate_analyzer.py` (add new integration test)

**Step 1: Write comprehensive end-to-end test**

Add to test file:

```python
def test_end_to_end_with_symlinks(self):
    """Full integration test with regular files and symlinks."""
    import tempfile
    import shutil

    dir_a = Path(tempfile.mkdtemp())
    dir_b = Path(tempfile.mkdtemp())

    try:
        # Create matching regular files
        make_file(dir_a, "docs/readme.md", b"readme content")
        make_file(dir_b, "docs/readme.md", b"readme content")

        # Create matching symlinks
        target = dir_a / "archive"
        target.mkdir()
        (target / "old.txt").write_text("old")

        (dir_a / "archive_link").symlink_to(target)
        (dir_b / "archive_link").symlink_to(target)

        # Create diverged symlinks
        target2 = dir_b / "archive2"
        target2.mkdir()
        (target2 / "new.txt").write_text("new")
        (dir_b / "old_link").symlink_to(target)

        # Create unique file
        make_file(dir_a, "unique_a.txt", b"only in A")

        # Run analysis
        result = cda.analyze(
            [("DirA", dir_a), ("DirB", dir_b)],
            mtime_fuzz=5.0,
            use_checksum=True,
            skip_hidden=True
        )

        # Verify structure
        assert "duplicate_groups" in result
        assert "conflict_groups" in result
        assert "symlinks" in result

        # Verify regular files found
        readme_dups = [d for d in result["duplicate_groups"] if d["name_orig"] == "readme.md"]
        assert len(readme_dups) > 0

        # Verify symlinks section
        symlinks = result["symlinks"]
        archive_symlinks = [s for s in symlinks if s["name_orig"] == "archive_link"]
        assert len(archive_symlinks) > 0
        assert archive_symlinks[0]["symlink_status"] == "target_identical"

        # Verify diverged symlink
        old_symlinks = [s for s in symlinks if s["name_orig"] == "old_link"]
        assert len(old_symlinks) > 0

    finally:
        shutil.rmtree(dir_a)
        shutil.rmtree(dir_b)
```

**Step 2: Run end-to-end test**

```bash
python -m pytest tests/test_cloud_duplicate_analyzer.py::TestSymlinkAnalysis::test_end_to_end_with_symlinks -v
```

Expected: `PASSED`

**Step 3: Run full test suite one more time**

```bash
python -m pytest tests/test_cloud_duplicate_analyzer.py -v
```

All tests must pass.

**Step 4: Commit**

```bash
git add tests/
git commit -m "test: add comprehensive end-to-end symlink integration test"
```

---

## Task 12: Manual Testing & Verification

**Files:**
- None (manual testing only)

**Step 1: Create test directories with symlinks**

```bash
mkdir -p /tmp/test_symlink_a /tmp/test_symlink_b
cd /tmp/test_symlink_a
echo "content" > file.txt
mkdir archive
echo "old" > archive/old.txt
ln -s $(pwd)/archive link_to_archive
cd /tmp/test_symlink_b
echo "content" > file.txt
ln -s /tmp/test_symlink_a/archive link_to_archive
```

**Step 2: Run tool**

```bash
cd /Users/nissim/dev/cloud-dedup
python3 src/cloud_duplicate_analyzer.py /tmp/test_symlink_a /tmp/test_symlink_b -o /tmp/symlink_test.html
```

**Step 3: Verify HTML output**

Open `/tmp/symlink_test.html` in browser and verify:
- Section 3: Symlinks show with `↪` symbol and targets
- Section 4: Any diverged symlinks listed
- Section 5: Regular duplicates shown, symlinks in separate subsection
- Unique files show with `◆` symbol

**Step 4: Verify JSON output**

```bash
python3 -m json.tool /tmp/symlink_test.json | grep -A 20 "symlinks"
```

Verify symlinks section contains target information.

**Step 5: No commit needed**

Manual testing only; if issues found, create bug fix commits.

---

## Execution Options

Plan complete and saved to `docs/plans/2026-02-28-symlink-detection-and-handling.md`. Two execution options:

**Option 1: Subagent-Driven (this session)**
- Fresh subagent per task
- Code review between tasks
- Fast iteration with immediate feedback
- Best for: catching issues early, learning as you go

**Option 2: Parallel Session (separate)**
- Open new session in isolated worktree
- Batch execution with checkpoints
- More deliberate, structured approach
- Best for: uninterrupted focus, fewer context switches

Which approach would you prefer?
