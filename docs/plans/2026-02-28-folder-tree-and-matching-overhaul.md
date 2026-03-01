# Folder Tree & Matching Overhaul Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace flat folder analysis with a collapsible subtree-rollup view, overhaul matching to always use MD5 with a two-dimensional content/version model, and add a "Files Requiring Action" section for genuinely conflicting files.

**Architecture:** All changes are confined to `src/cloud_duplicate_analyzer.py`. The `analyze()` function gains a second output collection (`conflict_groups`) and subtree rollup data attached to each `folder_comparison`. `render_html()` is reorganised into five sections in a new order. HTML uses the existing `<details>`/`<summary>` CSS already in the stylesheet for the collapsible tree.

**Tech Stack:** Python 3.8+ stdlib only. No new dependencies. Tests use `unittest` + `tempfile`.

---

## Background: New Data Model

### Matching replaces `confidence` with two independent dimensions

| content_match | version_status | Meaning | Action |
|---|---|---|---|
| `identical` | `same` | MD5 match + mtime within fuzz | safe to delete either copy |
| `identical` | `diverged` | MD5 match + mtime differs | safe (timestamp artifact) |
| `different` | `diverged` | MD5 mismatch + mtime differs | keep newer copy |
| `different` | `phantom` | MD5 mismatch + mtime within fuzz | keep both — dangerous |
| `unverified` | `same` | `--no-checksum`, mtime within fuzz | assumed match |
| `unverified` | `diverged` | `--no-checksum`, mtime differs | assumed match, maybe stale |

- `duplicate_groups`: content_match = `identical` or `unverified` → shown in Section 5
- `conflict_groups`: content_match = `different` → shown in Section 4 (Files Requiring Action)

### Section order

| # | Section |
|---|---------|
| 1 | File Counts |
| 2 | Duplicate Summary |
| 3 | Folder Structure Analysis (new tree) |
| 4 | Files Requiring Action (different·diverged + different·phantom only) |
| 5 | Duplicate Files (flat reference list) |

### New symbols in tree and tables

```
★  identical · same      — safe to delete either copy
✓  identical · diverged  — safe (sync timestamp artifact)
⚠  different · diverged  — keep newer copy          → links to §4
⚡ different · phantom   — keep both copies         → links to §4
→  unique to one service
~  unverified (--no-checksum mode)
```

---

## Task 1: Test scaffolding

**Files:**
- Create: `tests/test_cloud_duplicate_analyzer.py`

**Step 1: Create the test file with helper utilities**

```python
"""Tests for cloud_duplicate_analyzer.py"""
import sys, os, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
import cloud_duplicate_analyzer as cda


def make_file(directory, rel_path, content=b"data", mtime=None):
    """Write a temp file, optionally set mtime. Returns Path."""
    p = Path(directory) / rel_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


class TestClassifyPair(unittest.TestCase):
    pass  # populated in Task 2


class TestSubtreeRollup(unittest.TestCase):
    pass  # populated in Task 4


class TestAnalyzeIntegration(unittest.TestCase):
    pass  # populated in Task 3


if __name__ == "__main__":
    unittest.main()
```

**Step 2: Run to confirm import works**

```bash
python -m pytest tests/test_cloud_duplicate_analyzer.py -v
```

Expected: 0 tests collected, no errors.

**Step 3: Commit**

```bash
git add tests/test_cloud_duplicate_analyzer.py
git commit -m "test: scaffold test file for matching overhaul"
```

---

## Task 2: Replace `files_match` with `classify_pair`

**Files:**
- Modify: `src/cloud_duplicate_analyzer.py:162-183` (the `files_match` function)
- Modify: `tests/test_cloud_duplicate_analyzer.py`

### What changes

`files_match` returns a single confidence string (`'exact'`, `'likely'`, `''`). Replace it with `classify_pair` that returns `(content_match, version_status)` or `None` for no match.

**Step 1: Write failing tests in `TestClassifyPair`**

Replace the `pass` in `TestClassifyPair` with:

```python
def setUp(self):
    self.tmp = tempfile.mkdtemp()

def tearDown(self):
    import shutil; shutil.rmtree(self.tmp)

def _rec(self, name, content=b"hello", mtime=1000.0, subdir="a"):
    p = make_file(self.tmp, f"{subdir}/{name}", content, mtime)
    return {
        "name": name.lower(), "name_orig": name,
        "size": len(content), "mtime": mtime,
        "full_path": p, "folder": subdir,
    }

def test_identical_same(self):
    a = self._rec("f.txt", b"hello", mtime=1000.0, subdir="a")
    b = self._rec("f.txt", b"hello", mtime=1001.0, subdir="b")  # within 5s fuzz
    result = cda.classify_pair(a, b, mtime_fuzz=5.0, use_checksum=True)
    self.assertEqual(result, ("identical", "same"))

def test_identical_diverged(self):
    a = self._rec("f.txt", b"hello", mtime=1000.0, subdir="a")
    b = self._rec("f.txt", b"hello", mtime=2000.0, subdir="b")  # 1000s apart
    result = cda.classify_pair(a, b, mtime_fuzz=5.0, use_checksum=True)
    self.assertEqual(result, ("identical", "diverged"))

def test_different_diverged(self):
    a = self._rec("f.txt", b"hello", mtime=1000.0, subdir="a")
    b = self._rec("f.txt", b"world", mtime=2000.0, subdir="b")
    result = cda.classify_pair(a, b, mtime_fuzz=5.0, use_checksum=True)
    self.assertEqual(result, ("different", "diverged"))

def test_different_phantom(self):
    # Same size content so size matches, different bytes, same mtime
    a = self._rec("f.txt", b"hello", mtime=1000.0, subdir="a")
    b = self._rec("f.txt", b"world", mtime=1000.0, subdir="b")
    result = cda.classify_pair(a, b, mtime_fuzz=5.0, use_checksum=True)
    self.assertEqual(result, ("different", "phantom"))

def test_no_match_different_size(self):
    a = self._rec("f.txt", b"hello", subdir="a")
    b = self._rec("f.txt", b"hi", subdir="b")
    result = cda.classify_pair(a, b, mtime_fuzz=5.0, use_checksum=True)
    self.assertIsNone(result)

def test_no_checksum_mtime_same(self):
    a = self._rec("f.txt", b"hello", mtime=1000.0, subdir="a")
    b = self._rec("f.txt", b"world", mtime=1000.0, subdir="b")  # different content!
    result = cda.classify_pair(a, b, mtime_fuzz=5.0, use_checksum=False)
    self.assertEqual(result, ("unverified", "same"))

def test_no_checksum_mtime_differs(self):
    a = self._rec("f.txt", b"hello", mtime=1000.0, subdir="a")
    b = self._rec("f.txt", b"hello", mtime=2000.0, subdir="b")
    result = cda.classify_pair(a, b, mtime_fuzz=5.0, use_checksum=False)
    self.assertEqual(result, ("unverified", "diverged"))

def test_empty_file_always_identical_same(self):
    a = self._rec("empty.txt", b"", mtime=1000.0, subdir="a")
    b = self._rec("empty.txt", b"", mtime=9000.0, subdir="b")
    result = cda.classify_pair(a, b, mtime_fuzz=5.0, use_checksum=True)
    self.assertEqual(result, ("identical", "same"))
```

**Step 2: Run to confirm tests fail**

```bash
python -m pytest tests/test_cloud_duplicate_analyzer.py::TestClassifyPair -v
```

Expected: `AttributeError: module has no attribute 'classify_pair'`

**Step 3: Replace `files_match` with `classify_pair` in the source**

In `src/cloud_duplicate_analyzer.py`, replace the entire `files_match` function (lines 162–183) with:

```python
def classify_pair(a: dict, b: dict, mtime_fuzz: float, use_checksum: bool):
    """
    Compare two file records that share the same (name, size) index key.

    Returns (content_match, version_status) or None if name/size don't match.

      content_match : 'identical' | 'different' | 'unverified'
      version_status: 'same'      | 'diverged'  | 'phantom'

    'phantom' means mtime agrees but MD5 differs — the most dangerous case.
    """
    if a["name"] != b["name"] or a["size"] != b["size"]:
        return None

    mtime_same = abs(a["mtime"] - b["mtime"]) <= mtime_fuzz

    # Empty files have no content to hash; treat as identical.
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
        return ("different", "phantom" if mtime_same else "diverged")
```

**Step 4: Run tests**

```bash
python -m pytest tests/test_cloud_duplicate_analyzer.py::TestClassifyPair -v
```

Expected: All 8 tests pass.

**Step 5: Commit**

```bash
git add src/cloud_duplicate_analyzer.py tests/test_cloud_duplicate_analyzer.py
git commit -m "feat: replace files_match with classify_pair — two-dimensional content/version model"
```

---

## Task 3: Update `analyze()` — new groups and per-file classification

**Files:**
- Modify: `src/cloud_duplicate_analyzer.py:206-273` (the matching + divergence loop)
- Modify: `tests/test_cloud_duplicate_analyzer.py`

### What changes

- Call `classify_pair` instead of `files_match`.
- Split results into `duplicate_groups` (content_match = `identical` or `unverified`) and `conflict_groups` (content_match = `different`).
- Remove the separate version-divergence loop — `classify_pair` already sets `version_status`.
- Store `content_match`, `version_status`, and per-service `{"mtime", "size"}` on every group.
- Build a lookup `_file_classifications`: `(name_lower, folder) → {"content_match", "version_status", "conflict_id"}` used later by folder tree rendering.

**Step 1: Write integration tests in `TestAnalyzeIntegration`**

```python
def setUp(self):
    self.tmp = tempfile.mkdtemp()

def tearDown(self):
    import shutil; shutil.rmtree(self.tmp)

def _run(self, files_a, files_b, use_checksum=True, mtime_fuzz=5.0):
    """
    files_a / files_b: list of (rel_path, content_bytes, mtime)
    Returns result dict from analyze().
    """
    dir_a = Path(self.tmp) / "a"
    dir_b = Path(self.tmp) / "b"
    for rel, content, mtime in files_a:
        make_file(dir_a, rel, content, mtime)
    for rel, content, mtime in files_b:
        make_file(dir_b, rel, content, mtime)
    return cda.analyze(
        [("A", dir_a), ("B", dir_b)],
        mtime_fuzz=mtime_fuzz,
        use_checksum=use_checksum,
        skip_hidden=True,
    )

def test_identical_same_goes_to_duplicate_groups(self):
    r = self._run(
        [("doc.txt", b"hello", 1000.0)],
        [("doc.txt", b"hello", 1000.0)],
    )
    self.assertEqual(len(r["duplicate_groups"]), 1)
    self.assertEqual(len(r["conflict_groups"]), 0)
    g = r["duplicate_groups"][0]
    self.assertEqual(g["content_match"], "identical")
    self.assertEqual(g["version_status"], "same")

def test_different_phantom_goes_to_conflict_groups(self):
    r = self._run(
        [("doc.txt", b"hello", 1000.0)],
        [("doc.txt", b"world", 1000.0)],
    )
    self.assertEqual(len(r["duplicate_groups"]), 0)
    self.assertEqual(len(r["conflict_groups"]), 1)
    g = r["conflict_groups"][0]
    self.assertEqual(g["content_match"], "different")
    self.assertEqual(g["version_status"], "phantom")

def test_different_diverged_goes_to_conflict_groups(self):
    r = self._run(
        [("doc.txt", b"hello", 1000.0)],
        [("doc.txt", b"world", 9000.0)],
    )
    self.assertEqual(len(r["conflict_groups"]), 1)
    self.assertEqual(r["conflict_groups"][0]["version_status"], "diverged")

def test_identical_diverged_goes_to_duplicate_groups(self):
    r = self._run(
        [("doc.txt", b"hello", 1000.0)],
        [("doc.txt", b"hello", 9000.0)],
    )
    self.assertEqual(len(r["duplicate_groups"]), 1)
    self.assertEqual(r["duplicate_groups"][0]["version_status"], "diverged")

def test_conflict_group_has_per_service_size_and_mtime(self):
    r = self._run(
        [("doc.txt", b"hello", 1000.0)],
        [("doc.txt", b"world", 9000.0)],
    )
    g = r["conflict_groups"][0]
    self.assertIn("A", g["service_details"])
    self.assertIn("size", g["service_details"]["A"])
    self.assertIn("mtime", g["service_details"]["A"])
```

**Step 2: Run to confirm tests fail**

```bash
python -m pytest tests/test_cloud_duplicate_analyzer.py::TestAnalyzeIntegration -v
```

Expected: Failures referencing missing `conflict_groups` key and missing `content_match` field.

**Step 3: Rewrite the matching section of `analyze()`**

Replace lines 218–272 (from `duplicate_groups = []` through the end of the version-divergence loop) with:

```python
    duplicate_groups = []   # content_match: 'identical' or 'unverified'
    conflict_groups  = []   # content_match: 'different'
    seen_keys = set()

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
        content_rank  = {"identical": 0, "unverified": 1, "different": 2}
        version_rank  = {"same": 0, "diverged": 1, "phantom": 2}
        group_content = "identical"
        group_version = "same"
        all_matched   = True

        for la, lb in combinations(label_list, 2):
            result = classify_pair(
                present_in[la], present_in[lb], mtime_fuzz, use_checksum
            )
            if result is None:
                all_matched = False
                break
            cm, vs = result
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
                "size":  present_in[label]["size"],
                "mtime": fmt_ts(present_in[label]["mtime"]),
                "mtime_raw": present_in[label]["mtime"],
            }
            for label in present_in
        }

        group = {
            "rel_path":       rel,
            "name_orig":      name_orig,
            "size":           size,
            "matches":        {label: present_in[label] for label in present_in},
            "content_match":  group_content,
            "version_status": group_version,
            "service_details": service_details,
            # Keep newest_in for backward compat in stdout summary
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

    # Build a lookup for the folder tree renderer:
    # key: (name_lower, folder_str) → {content_match, version_status, group_index}
    _file_classifications = {}
    for i, g in enumerate(conflict_groups):
        rp = Path(g["rel_path"])
        folder = str(rp.parent) if str(rp.parent) != "." else "(root)"
        _file_classifications[(g["rel_path"].split("/")[-1].lower(), folder)] = {
            "content_match":  g["content_match"],
            "version_status": g["version_status"],
            "conflict_index": i,
        }
    for g in duplicate_groups:
        rp = Path(g["rel_path"])
        folder = str(rp.parent) if str(rp.parent) != "." else "(root)"
        _file_classifications[(g["rel_path"].split("/")[-1].lower(), folder)] = {
            "content_match":  g["content_match"],
            "version_status": g["version_status"],
            "conflict_index": None,
        }
```

Also update the return dict at line 369 to include `conflict_groups` and `_file_classifications`:

```python
    return {
        ...existing keys...,
        "conflict_groups": conflict_groups,
        "_file_classifications": _file_classifications,
        ...
    }
```

Remove `"all_services_count"` references if they no longer apply (keep for now, compute from duplicate_groups only).

**Step 4: Run tests**

```bash
python -m pytest tests/test_cloud_duplicate_analyzer.py::TestAnalyzeIntegration -v
```

Expected: All 5 tests pass.

**Step 5: Smoke-test the tool end-to-end**

```bash
python3 src/cloud_duplicate_analyzer.py "A:/tmp/test_a" "B:/tmp/test_b" -o /tmp/smoke_test.html
```

Confirm it runs without error and produces output files.

**Step 6: Commit**

```bash
git add src/cloud_duplicate_analyzer.py tests/test_cloud_duplicate_analyzer.py
git commit -m "feat: split analyze() into duplicate_groups + conflict_groups with content/version dimensions"
```

---

## Task 4: Subtree rollups in `analyze()`

**Files:**
- Modify: `src/cloud_duplicate_analyzer.py` (after the folder_comparisons loop, before the return)
- Modify: `tests/test_cloud_duplicate_analyzer.py`

### What changes

After `folder_comparisons` is built, compute for each folder:
- `subtree_total_files`: total files across all subfolders rooted here
- `subtree_identical_count`: files in subtrees classified as "identical"
- `subtree_status`: `"identical"` if all descendant folders are identical, `"partial"`, or `"overlap"`

Also compute `safe_to_delete_roots`: the highest-level folders where `subtree_status == "identical"` (no ancestor also has `subtree_status == "identical"`).

**Step 1: Write subtree rollup tests**

```python
# In TestSubtreeRollup
def setUp(self):
    self.tmp = tempfile.mkdtemp()

def tearDown(self):
    import shutil; shutil.rmtree(self.tmp)

def _run_tree(self, structure_a, structure_b):
    """
    structure: list of (rel_path, content, mtime)
    """
    dir_a = Path(self.tmp) / "a"
    dir_b = Path(self.tmp) / "b"
    for rel, content, mtime in structure_a:
        make_file(dir_a, rel, content, mtime)
    for rel, content, mtime in structure_b:
        make_file(dir_b, rel, content, mtime)
    return cda.analyze(
        [("A", dir_a), ("B", dir_b)],
        mtime_fuzz=5.0, use_checksum=True, skip_hidden=True,
    )

def test_fully_identical_subtree_detected(self):
    r = self._run_tree(
        [("photos/2020/jan.jpg", b"img1", 1000.0),
         ("photos/2020/feb.jpg", b"img2", 1000.0),
         ("photos/2021/mar.jpg", b"img3", 1000.0)],
        [("photos/2020/jan.jpg", b"img1", 1000.0),
         ("photos/2020/feb.jpg", b"img2", 1000.0),
         ("photos/2021/mar.jpg", b"img3", 1000.0)],
    )
    fc_by_path = {fc["folder_path"]: fc for fc in r["folder_comparisons"]}
    self.assertEqual(fc_by_path["photos"]["subtree_status"], "identical")
    self.assertIn("photos", [s["folder_path"] for s in r["safe_to_delete_roots"]])

def test_partial_subtree_not_in_safe_roots(self):
    r = self._run_tree(
        [("docs/work/a.txt", b"aaa", 1000.0),
         ("docs/personal/b.txt", b"bbb", 1000.0)],
        [("docs/work/a.txt", b"aaa", 1000.0),
         ("docs/personal/c.txt", b"ccc", 1000.0)],  # different file in personal
    )
    safe_paths = [s["folder_path"] for s in r["safe_to_delete_roots"]]
    self.assertNotIn("docs", safe_paths)

def test_safe_root_is_highest_level_only(self):
    """When Photos/ is fully identical, Photos/2020/ should not also appear in safe_roots."""
    r = self._run_tree(
        [("photos/2020/jan.jpg", b"img1", 1000.0)],
        [("photos/2020/jan.jpg", b"img1", 1000.0)],
    )
    safe_paths = [s["folder_path"] for s in r["safe_to_delete_roots"]]
    self.assertIn("photos", safe_paths)
    self.assertNotIn("photos/2020", safe_paths)
```

**Step 2: Run to confirm tests fail**

```bash
python -m pytest tests/test_cloud_duplicate_analyzer.py::TestSubtreeRollup -v
```

Expected: `KeyError: 'subtree_status'`

**Step 3: Add subtree rollup computation after the folder_comparisons loop**

In `src/cloud_duplicate_analyzer.py`, insert after the `folder_comparisons.append(...)` block and `rel_counts` computation, before the `return` statement:

```python
    # ── subtree rollups ───────────────────────────────────────────
    # For each folder, find all descendant folder_comparisons and
    # compute aggregate status.
    fc_by_path = {fc["folder_path"]: fc for fc in folder_comparisons}
    all_fc_paths = set(fc_by_path.keys())

    def get_descendants(folder_path):
        """Return all folder_comparison paths that are descendants of folder_path."""
        prefix = folder_path + "/"  if folder_path != "(root)" else ""
        return [p for p in all_fc_paths if p.startswith(prefix) or p == folder_path]

    for fc in folder_comparisons:
        descendants = get_descendants(fc["folder_path"])
        desc_fcs = [fc_by_path[p] for p in descendants]

        all_identical = all(d["relationship"] == "identical" for d in desc_fcs)
        any_overlap   = any(d["relationship"] == "overlap" for d in desc_fcs)

        if all_identical:
            fc["subtree_status"] = "identical"
        elif any_overlap:
            fc["subtree_status"] = "overlap"
        else:
            fc["subtree_status"] = "partial"

        fc["subtree_total_files"] = sum(d["total_unique_files"] for d in desc_fcs
                                         if d["folder_path"] != fc["folder_path"])
        fc["subtree_total_files"] += fc["total_unique_files"]

    # ── safe-to-delete roots ──────────────────────────────────────
    # Highest-level folders whose entire subtree is identical.
    # Exclude any folder whose ancestor is also a safe root.
    identical_roots = [
        fc for fc in folder_comparisons if fc["subtree_status"] == "identical"
    ]
    safe_to_delete_roots = []
    for fc in identical_roots:
        path = fc["folder_path"]
        # Check no ancestor path is also an identical root
        has_identical_ancestor = any(
            path.startswith(other["folder_path"] + "/")
            for other in identical_roots
            if other["folder_path"] != path
        )
        if not has_identical_ancestor:
            safe_to_delete_roots.append(fc)
```

Add `"safe_to_delete_roots": safe_to_delete_roots` to the return dict.

**Step 4: Run tests**

```bash
python -m pytest tests/test_cloud_duplicate_analyzer.py::TestSubtreeRollup -v
```

Expected: All 3 tests pass.

**Step 5: Run full test suite**

```bash
python -m pytest tests/ -v
```

Expected: All tests pass.

**Step 6: Commit**

```bash
git add src/cloud_duplicate_analyzer.py tests/test_cloud_duplicate_analyzer.py
git commit -m "feat: add subtree rollup and safe-to-delete root detection to folder analysis"
```

---

## Task 5: Render Section 4 — Files Requiring Action

**Files:**
- Modify: `src/cloud_duplicate_analyzer.py` — `render_html()`, section 4 block

### What changes

Replace the current Section 4 (Version-Diverged Files, lines 506–526) with a new "Files Requiring Action" table showing `conflict_groups` only. Each row gets an HTML anchor `id="action-N"` for cross-referencing from the folder tree.

Per row: filename, folder path, status badge, and for each service: size + formatted mtime on two lines.

**Step 1: Add CSS for the new section**

In the `CSS` string (around line 385), add after the existing `.warn-row` rule:

```css
.action-row td { background:#fff0f0 !important; }
.phantom-row td { background:#fff8e1 !important; }
.action-anchor { font-size:11px; color:#888; text-decoration:none; }
.service-detail { font-size:11px; line-height:1.6; }
```

**Step 2: Replace the section 4 render block**

Find the comment `# ── section 4: version diverged ──` and replace the entire block through `parts.append("</table>")` with:

```python
    # ── section 4: files requiring action ──
    conflicts = result.get("conflict_groups", [])
    parts.append(f"<h2>4. Files Requiring Action ({len(conflicts)} files)</h2>")
    if not conflicts:
        parts.append("<p>No content conflicts found — all matched files have identical content.</p>")
    else:
        parts.append(
            "<p>These files share a name and size across services but have <strong>different content</strong>. "
            "Review each before deleting any copy.</p>"
            "<p>"
            "<strong>⚠ different · diverged</strong> — content differs, timestamps differ; keep the newer copy.<br>"
            "<strong>⚡ different · phantom</strong> — content differs despite matching timestamps; keep both copies.</p>"
        )
        svc_headers = "".join(f'<th>{html.escape(l)}</th>' for l in labels)
        parts.append(
            f'<table><tr><th>File</th><th>Folder</th><th>Status</th>{svc_headers}</tr>'
        )
        for i, g in enumerate(sorted(conflicts, key=lambda x: x["rel_path"])):
            rp = Path(g["rel_path"])
            folder_str = str(rp.parent) if str(rp.parent) != "." else "(root)"
            vs = g["version_status"]
            symbol = "⚡" if vs == "phantom" else "⚠"
            row_cls = "phantom-row" if vs == "phantom" else "action-row"
            status_badge = badge(f"different · {vs}", f"{'phantom' if vs=='phantom' else 'diverged'}")

            svc_cells = ""
            for label in labels:
                if label in g["service_details"]:
                    det = g["service_details"][label]
                    svc_cells += (
                        f'<td class="service-detail">'
                        f'{human_size(det["size"])}<br>'
                        f'{html.escape(det["mtime"])}</td>'
                    )
                else:
                    svc_cells += '<td style="color:#aaa">—</td>'

            parts.append(
                f'<tr class="{row_cls}" id="action-{i}">'
                f'<td><strong>{symbol} {html.escape(g["name_orig"])}</strong></td>'
                f'<td><code>{html.escape(folder_str)}</code></td>'
                f'<td>{status_badge}</td>'
                f'{svc_cells}</tr>'
            )
        parts.append("</table>")
```

**Step 3: Smoke-test renders correctly**

```bash
python3 src/cloud_duplicate_analyzer.py "A:/tmp/test_a" "B:/tmp/test_b" -o /tmp/smoke_test.html
open /tmp/smoke_test.html
```

Verify Section 4 renders with the new table structure.

**Step 4: Commit**

```bash
git add src/cloud_duplicate_analyzer.py
git commit -m "feat: add Files Requiring Action section (different·diverged and different·phantom)"
```

---

## Task 6: Render Section 3 — Folder Structure Analysis (tree view)

**Files:**
- Modify: `src/cloud_duplicate_analyzer.py` — `render_html()`, section 5 block (becomes section 3)
- Modify: CSS string

### What changes

Replace the current Section 5 flat tables with:
1. An actionability panel listing `safe_to_delete_roots`
2. A collapsible folder tree using `<details>`/`<summary>` (CSS already supports these)

Each folder node:
- Summary line: symbol + folder name + subtree_status + file count + subfolder count
- When expanded: shows file list with ★/✓/⚠/⚡/→ per file, links to §4 for ⚠/⚡

**Step 1: Add tree CSS**

Append to the `CSS` string:

```css
.tree-root { margin: 8px 0; }
.tree-node details { margin-left: 20px; border-left: 2px solid #e0e8f0; padding-left: 8px; }
.tree-node summary { list-style: none; cursor: pointer; padding: 4px 0; }
.tree-node summary::-webkit-details-marker { display: none; }
.tree-file { font-size: 12px; font-family: monospace; padding: 2px 0 2px 8px; }
.tree-file-section { font-size: 11px; font-weight: bold; color: #555;
                     margin: 8px 0 4px 8px; border-bottom: 1px solid #eee; }
.sym-identical-same     { color: #28a745; }
.sym-identical-diverged { color: #17a2b8; }
.sym-different-diverged { color: #dc3545; }
.sym-different-phantom  { color: #fd7e14; }
.sym-unique             { color: #6c757d; }
```

**Step 2: Build the tree render helper functions**

Add these helper functions immediately before `render_html`:

```python
def _subtree_symbol(subtree_status: str) -> str:
    return {"identical": "★", "partial": "~", "overlap": "✗"}.get(subtree_status, "?")


def _file_symbol(content_match: str, version_status: str) -> tuple[str, str]:
    """Returns (symbol, css_class)."""
    if content_match == "identical" and version_status == "same":
        return "★", "sym-identical-same"
    if content_match == "identical" and version_status == "diverged":
        return "✓", "sym-identical-diverged"
    if content_match == "different" and version_status == "diverged":
        return "⚠", "sym-different-diverged"
    if content_match == "different" and version_status == "phantom":
        return "⚡", "sym-different-phantom"
    # unverified
    return "~", "sym-unique"


def _build_tree(folder_comparisons: list[dict]) -> dict:
    """
    Build a nested dict tree from flat folder_comparisons.
    Returns {folder_name: {"_fc": fc_dict, children: {name: ...}}}
    Roots are top-level folders (no "/" in path, or path == "(root)").
    """
    tree = {}
    # Sort so parents are processed before children
    for fc in sorted(folder_comparisons, key=lambda x: x["folder_path"]):
        parts = fc["folder_path"].split("/") if fc["folder_path"] != "(root)" else ["(root)"]
        node = tree
        for part in parts[:-1]:
            node = node.setdefault(part, {}).setdefault("_children", {})
        leaf = parts[-1]
        if leaf not in node:
            node[leaf] = {}
        node[leaf]["_fc"] = fc
        node[leaf].setdefault("_children", {})
    return tree
```

**Step 3: Replace the section 5 render block with the new section 3**

Find `# ── section 5: folder comparisons ──` and replace everything through the overlap table closing `</table>` with:

```python
    # ── section 3: folder structure analysis ──
    fc_list = result["folder_comparisons"]
    safe_roots = result.get("safe_to_delete_roots", [])
    file_cls = result.get("_file_classifications", {})

    parts.append(f"<h2>3. Folder Structure Analysis ({len(fc_list)} shared folders)</h2>")

    # ── actionability panel ──
    parts.append("<h3>Fully duplicated subtrees — safe to delete</h3>")
    if not safe_roots:
        parts.append("<p>No folder subtrees are fully identical across all services.</p>")
    else:
        parts.append(
            "<p>Each subtree below is 100% identical across all copies. "
            "Deleting from any one service is safe.</p>"
        )
        svc_headers = "".join(f'<th>{html.escape(l)}</th>' for l in labels)
        parts.append(
            f'<table><tr><th>Folder</th>{svc_headers}<th>Files in subtree</th></tr>'
        )
        for fc in sorted(safe_roots, key=lambda x: x["folder_path"]):
            svc_cells = "".join(
                f'<td>{"✓" if l in fc["services_present"] else "—"}</td>'
                for l in labels
            )
            parts.append(
                f'<tr><td><code>{html.escape(fc["folder_path"])}</code></td>'
                f'{svc_cells}'
                f'<td>{fc["subtree_total_files"]:,}</td></tr>'
            )
        parts.append("</table>")

    # ── folder tree ──
    parts.append("<h3>Folder tree</h3>")
    parts.append(
        "<p>Expand any folder to see file-level detail. "
        "Folders marked ★ are fully identical; ~ are partially duplicated; ✗ have conflicts.</p>"
    )

    # Build per-folder file lists from scanned records + classifications
    # folder_path → {label → [record]}
    folder_label_files = defaultdict(lambda: defaultdict(list))
    for label, recs in result.get("_scanned_records", {}).items():
        for r in recs:
            folder_label_files[r["folder"]][label].append(r)

    def render_folder_node(name: str, node: dict, depth: int) -> list[str]:
        fc = node.get("_fc")
        children = node.get("_children", {})
        out = []

        if fc is None and not children:
            return out

        sym = _subtree_symbol(fc["subtree_status"]) if fc else "?"
        folder_path = fc["folder_path"] if fc else name
        child_count = len(children)
        file_count = fc["total_unique_files"] if fc else 0
        status_cls = {
            "identical": "sym-identical-same",
            "partial": "sym-identical-diverged",
            "overlap": "sym-different-diverged",
        }.get(fc["subtree_status"] if fc else "", "")

        summary = (
            f'<span class="{status_cls}">{sym}</span> '
            f'<strong>{html.escape(name)}/</strong>'
            f' &nbsp;<span style="color:#888;font-size:12px">'
            f'{fc["subtree_status"] if fc else ""}'
            f' · {file_count} files'
            + (f' · {child_count} subfolders' if child_count else '')
            + '</span>'
        )

        out.append(f'<details class="tree-node"><summary>{summary}</summary>')

        # File list for this folder
        if fc:
            folder_key = fc["folder_path"]
            all_file_names = set()
            per_label = folder_label_files.get(folder_key, {})
            for recs in per_label.values():
                for r in recs:
                    all_file_names.add(r["name"])

            # Partition files: in-multiple vs unique-to-one
            in_multiple = []
            unique_to = defaultdict(list)
            for fname in sorted(all_file_names):
                labels_with = [l for l in labels if any(r["name"] == fname for r in per_label.get(l, []))]
                if len(labels_with) > 1:
                    cls_info = file_cls.get((fname, folder_key))
                    in_multiple.append((fname, labels_with, cls_info))
                else:
                    unique_to[labels_with[0]].append(fname) if labels_with else None

            if in_multiple:
                out.append('<div class="tree-file-section">In all services</div>')
                for fname, _, cls_info in in_multiple:
                    if cls_info:
                        sym2, sym_cls = _file_symbol(cls_info["content_match"], cls_info["version_status"])
                        link = ""
                        if cls_info["conflict_index"] is not None:
                            link = f' <a class="action-anchor" href="#action-{cls_info["conflict_index"]}">→ §4</a>'
                        out.append(
                            f'<div class="tree-file">'
                            f'<span class="{sym_cls}">{sym2}</span> '
                            f'{html.escape(fname)}{link}</div>'
                        )
                    else:
                        out.append(f'<div class="tree-file">· {html.escape(fname)}</div>')

            for label in labels:
                ufiles = unique_to.get(label, [])
                if ufiles:
                    out.append(f'<div class="tree-file-section">Only in {html.escape(label)}</div>')
                    for fname in ufiles:
                        out.append(f'<div class="tree-file"><span class="sym-unique">→</span> {html.escape(fname)}</div>')

        # Recurse into children
        for child_name in sorted(children):
            out.extend(render_folder_node(child_name, children[child_name], depth + 1))

        out.append("</details>")
        return out

    tree = _build_tree(fc_list)
    parts.append('<div class="tree-root">')
    for root_name in sorted(tree):
        parts.extend(render_folder_node(root_name, tree[root_name], 0))
    parts.append("</div>")

    parts.append(
        "<p style='font-size:12px;color:#888'>"
        "★ identical · same &nbsp;|&nbsp; "
        "✓ identical · diverged &nbsp;|&nbsp; "
        "⚠ different · diverged &nbsp;|&nbsp; "
        "⚡ different · phantom &nbsp;|&nbsp; "
        "→ unique to one service</p>"
    )
```

**Note:** The tree renderer references `result["_scanned_records"]`. Add this to the `analyze()` return dict:

```python
"_scanned_records": {label: scanned[label] for label in labels},
```

**Step 4: Update section numbering throughout `render_html()`**

- Old Section 3 (Duplicate Files) → becomes Section 5: update its heading string
- Old Section 4 (action) → already updated to Section 4 in Task 5
- Old Section 5 (folder) → now Section 3 (done above)
- Update stdout `print(f"... see Section 4 ...")` in `main()` to reference correct section

**Step 5: Smoke-test the tree**

```bash
python3 src/cloud_duplicate_analyzer.py "A:/tmp/test_a" "B:/tmp/test_b" -o /tmp/smoke_test.html
open /tmp/smoke_test.html
```

Verify:
- Expand/collapse works on folder nodes
- Safe-to-delete panel shows at top of section
- Files show ★/✓/⚠/⚡ symbols
- ⚠/⚡ files have `→ §4` links that jump to correct row in Section 4

**Step 6: Commit**

```bash
git add src/cloud_duplicate_analyzer.py
git commit -m "feat: replace flat folder tables with collapsible subtree-rollup tree view"
```

---

## Task 7: Update Section 5 — Duplicate Files (updated labels)

**Files:**
- Modify: `src/cloud_duplicate_analyzer.py` — section 3 render block (now becomes section 5)

### What changes

Replace `confidence` column with `content_match · version_status` badges. Add badge styles for `unverified`. Remove "Version" column (now redundant — version_status is part of the combined badge).

**Step 1: Add unverified badge CSS**

```css
.badge-unverified { background:#e2e3e5; color:#383d41; }
.badge-identical  { background:#d4edda; color:#155724; }
```

**Step 2: Update the Section 5 render block**

Find `# ── section 3: duplicate file list ──` (now renumbered to section 5) and update:
- Change heading to `"5. Duplicate Files"`
- Replace `match_cell = badge(g["confidence"])` with:
  ```python
  match_label = f'{g["content_match"]} · {g["version_status"]}'
  match_cell  = badge(match_label, g["content_match"])
  ```
- Remove the `version_cell` and `<th>Version</th>` column (the combined badge covers it)
- Remove `row_cls` highlight for diverged — Section 4 now handles action items, Section 5 is reference only

**Step 3: Smoke-test Section 5**

Open the generated report and confirm Section 5 shows `identical · same`, `identical · diverged`, `unverified · same` etc. as badges.

**Step 4: Commit**

```bash
git add src/cloud_duplicate_analyzer.py
git commit -m "feat: update Duplicate Files section with content/version badge labels"
```

---

## Task 8: Update docstring, stdout summary, and docs

**Files:**
- Modify: `src/cloud_duplicate_analyzer.py` — module docstring (lines 4–12) and `main()` stdout summary
- Modify: `docs/how-it-works.md`
- Modify: `docs/report-format.md`

**Step 1: Update the module docstring**

Replace lines 8–11:
```python
  • Which files are duplicated across directories (by name+size, confirmed by MD5 checksum)
  • Content match: identical (MD5 confirmed) | different (conflict) | unverified (--no-checksum)
  • Version status: same (mtime agrees) | diverged (mtime differs) | phantom (mtime agrees but content differs)
  • How folder sub-trees relate, with subtree rollup and safe-to-delete identification
```

Remove `--no-checksum` description text that says "rely only on name+size+mtime" and update to:
```
    --no-checksum          Skip MD5 checksums. Matched files are labelled 'unverified'
                           rather than 'identical'. The dangerous 'phantom' case
                           (same metadata, different content) cannot be detected.
```

**Step 2: Update stdout summary in `main()`**

Replace the diverged-files summary line:
```python
    conflicts = result.get("conflict_groups", [])
    if conflicts:
        print(f"\n  ⚠  {len(conflicts)} file(s) require action (different content) — see Section 4 of report")
```

**Step 3: Update `docs/how-it-works.md`**

Replace the "Duplicate Matching" and "Version Divergence" sections with the new two-dimensional model. Key points:
- MD5 is now always computed for name+size candidates (not just mtime-mismatches)
- `--no-checksum` produces `unverified` labels, cannot detect phantom conflicts
- Table of the four combinations as documented above in the Background section

**Step 4: Update `docs/report-format.md`**

Update section descriptions and JSON schema to reflect:
- New section order (1–5)
- `conflict_groups` array in JSON
- `content_match` and `version_status` fields replacing `confidence`
- `safe_to_delete_roots` in folder_comparisons
- `subtree_status`, `subtree_total_files` on each folder_comparison

**Step 5: Commit**

```bash
git add src/cloud_duplicate_analyzer.py docs/how-it-works.md docs/report-format.md
git commit -m "docs: update module docstring, stdout summary, and docs for new matching model and tree view"
```

---

## Final verification

```bash
# Run full test suite
python -m pytest tests/ -v

# Smoke-test with real data
python3 src/cloud_duplicate_analyzer.py "A:/tmp/test_a" "B:/tmp/test_b" -o /tmp/final_check.html
open /tmp/final_check.html
```

Verify:
- [ ] All tests pass
- [ ] Section 3 shows safe-to-delete panel + collapsible tree
- [ ] Section 4 shows only different·diverged and different·phantom files with per-service size + date
- [ ] Section 4 rows have anchor IDs (`id="action-N"`)
- [ ] Section 3 tree ⚠/⚡ links jump to correct Section 4 row
- [ ] Section 5 shows `identical · same` / `identical · diverged` / `unverified · same` badges
- [ ] `--no-checksum` run labels matches as `unverified`
- [ ] No `exact` or `likely` labels appear anywhere in output
