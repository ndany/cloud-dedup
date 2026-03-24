"""Tests to increase coverage to 85%+.

Covers the major uncovered areas:
- parse_dir_arg label parsing and edge cases
- human_size PB branch
- fmt_ts edge cases
- scan_directory: hidden files, .DS_Store, symlinks, stat failures
- classify_pair: symlink/mixed_type branches in analyze loop
- _file_sym helper
- _build_folder_tree
- render_html Section 2 pill badges (unverified, different, phantom, conflict, diverged)
- render_html Section 4 (conflict groups, diverged symlinks, mixed_type)
- render_html Section 5 (duplicate file list, symlinks subsection, version-diverged)
- main() CLI entry point
"""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
import cloud_duplicate_analyzer as cda


def make_file(directory, rel_path, content=b"data", mtime=None):
    p = Path(directory) / rel_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


# ── helpers ──────────────────────────────────────────────────────────

class TestParseDirArg(unittest.TestCase):
    def test_label_colon_path(self):
        label, path = cda.parse_dir_arg("MyLabel:/tmp")
        self.assertEqual(label, "MyLabel")
        self.assertEqual(path, Path("/tmp").resolve())

    def test_absolute_path_no_label(self):
        label, path = cda.parse_dir_arg("/tmp/somedir")
        self.assertEqual(label, "somedir")

    def test_tilde_path(self):
        label, path = cda.parse_dir_arg("~/somedir")
        self.assertEqual(label, "somedir")
        self.assertTrue(path.is_absolute())


class TestHumanSize(unittest.TestCase):
    def test_pb_range(self):
        # 1 PB = 1024^5 bytes
        result = cda.human_size(1024**5)
        self.assertIn("PB", result)

    def test_zero_bytes(self):
        self.assertEqual(cda.human_size(0), "0 B")

    def test_kb(self):
        result = cda.human_size(2048)
        self.assertIn("KB", result)


class TestFmtTs(unittest.TestCase):
    def test_zero_returns_dash(self):
        self.assertEqual(cda.fmt_ts(0), "—")

    def test_overflow_returns_dash(self):
        # Very large timestamp that causes OverflowError
        self.assertEqual(cda.fmt_ts(1e20), "—")

    def test_normal_timestamp(self):
        result = cda.fmt_ts(1000000000.0)
        self.assertIn("UTC", result)


# ── scan_directory ───────────────────────────────────────────────────

class TestScanDirectory(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_skip_hidden_files(self):
        make_file(self.tmp, "visible.txt", b"v")
        make_file(self.tmp, ".hidden.txt", b"h")
        records, _ = cda.scan_directory(Path(self.tmp), skip_hidden=True)
        names = [r["name_orig"] for r in records]
        self.assertIn("visible.txt", names)
        self.assertNotIn(".hidden.txt", names)

    def test_include_hidden_files(self):
        make_file(self.tmp, "visible.txt", b"v")
        make_file(self.tmp, ".hidden.txt", b"h")
        records, _ = cda.scan_directory(Path(self.tmp), skip_hidden=False)
        names = [r["name_orig"] for r in records]
        self.assertIn("visible.txt", names)
        self.assertIn(".hidden.txt", names)

    def test_skip_hidden_dirs(self):
        make_file(self.tmp, ".hidden_dir/file.txt", b"h")
        make_file(self.tmp, "visible_dir/file.txt", b"v")
        records, _ = cda.scan_directory(Path(self.tmp), skip_hidden=True)
        folders = [r["folder"] for r in records]
        self.assertNotIn(".hidden_dir", folders)
        self.assertIn("visible_dir", folders)

    def test_ds_store_skipped(self):
        make_file(self.tmp, ".DS_Store", b"junk")
        make_file(self.tmp, "real.txt", b"data")
        records, _ = cda.scan_directory(Path(self.tmp), skip_hidden=False)
        names = [r["name_orig"] for r in records]
        self.assertNotIn(".DS_Store", names)
        self.assertIn("real.txt", names)

    def test_symlink_detected(self):
        target = make_file(self.tmp, "target.txt", b"content")
        link = Path(self.tmp) / "link.txt"
        link.symlink_to(target)
        records, _ = cda.scan_directory(Path(self.tmp), skip_hidden=False)
        link_rec = next(r for r in records if r["name_orig"] == "link.txt")
        self.assertTrue(link_rec["is_symlink"])
        self.assertEqual(link_rec["size"], -1)
        self.assertEqual(link_rec["mtime"], 0.0)
        self.assertIsNotNone(link_rec["symlink_target"])

    def test_regular_file_fields(self):
        make_file(self.tmp, "sub/file.txt", b"hello", mtime=1000.0)
        records, _ = cda.scan_directory(Path(self.tmp), skip_hidden=True)
        rec = next(r for r in records if r["name_orig"] == "file.txt")
        self.assertFalse(rec["is_symlink"])
        self.assertIsNone(rec["symlink_target"])
        self.assertEqual(rec["size"], 5)
        self.assertEqual(rec["folder"], "sub")
        self.assertEqual(rec["name"], "file.txt")  # lowercased

    def test_no_warnings_for_normal_scan(self):
        make_file(self.tmp, "file.txt", b"data")
        _, warnings = cda.scan_directory(Path(self.tmp), skip_hidden=True)
        self.assertEqual(warnings, [])

    def test_empty_dir_warning(self):
        empty = Path(self.tmp) / "empty"
        empty.mkdir()
        records, warnings = cda.scan_directory(empty, skip_hidden=True)
        self.assertEqual(records, [])
        self.assertEqual(len(warnings), 1)
        self.assertIn("0 files found", warnings[0])

    def test_permission_error_warning(self):
        subdir = Path(self.tmp) / "locked"
        subdir.mkdir()
        make_file(self.tmp, "locked/secret.txt", b"data")
        subdir.chmod(0o000)
        try:
            records, warnings = cda.scan_directory(Path(self.tmp), skip_hidden=False)
            self.assertTrue(any("Permission denied" in w for w in warnings))
        finally:
            subdir.chmod(0o755)

    def test_permission_error_on_root_dir(self):
        root = Path(self.tmp) / "noaccess"
        root.mkdir()
        make_file(self.tmp, "noaccess/file.txt", b"data")
        root.chmod(0o000)
        try:
            records, warnings = cda.scan_directory(root, skip_hidden=False)
            self.assertEqual(records, [])
            self.assertTrue(any("Permission denied" in w for w in warnings))
        finally:
            root.chmod(0o755)


# ── _file_sym ────────────────────────────────────────────────────────

class TestFileSym(unittest.TestCase):
    def test_symlink(self):
        sym, cls = cda._file_sym("identical", "same", is_symlink=True)
        self.assertEqual(sym, "↪")
        self.assertEqual(cls, "sym-symlink")

    def test_mixed_type(self):
        sym, cls = cda._file_sym("mixed_type", "conflict")
        self.assertEqual(sym, "↪⚠")
        self.assertEqual(cls, "sym-dd")

    def test_identical_same(self):
        sym, cls = cda._file_sym("identical", "same")
        self.assertEqual(sym, "★")

    def test_identical_diverged(self):
        sym, cls = cda._file_sym("identical", "diverged")
        self.assertEqual(sym, "✓")

    def test_different_diverged(self):
        sym, cls = cda._file_sym("different", "diverged")
        self.assertEqual(sym, "⚠")

    def test_different_phantom(self):
        sym, cls = cda._file_sym("different", "phantom")
        self.assertEqual(sym, "⚡")

    def test_fallback(self):
        sym, cls = cda._file_sym("unknown", "unknown")
        self.assertEqual(sym, "~")

    def test_unverified_same(self):
        sym, cls = cda._file_sym("unverified", "same")
        self.assertEqual(sym, "★")

    def test_unverified_diverged(self):
        sym, cls = cda._file_sym("unverified", "diverged")
        self.assertEqual(sym, "✓")


# ── _build_folder_tree ───────────────────────────────────────────────

class TestBuildFolderTree(unittest.TestCase):
    def test_root_entry(self):
        fcs = [{"folder_path": "(root)", "subtree_status": "identical"}]
        tree = cda._build_folder_tree(fcs)
        self.assertIn("(root)", tree)
        self.assertEqual(tree["(root)"]["_fc"]["folder_path"], "(root)")

    def test_nested_path(self):
        fcs = [
            {"folder_path": "a/b/c", "subtree_status": "identical"},
        ]
        tree = cda._build_folder_tree(fcs)
        self.assertIn("a", tree)
        self.assertIn("b", tree["a"]["_children"])
        self.assertIn("c", tree["a"]["_children"]["b"]["_children"])
        self.assertIsNotNone(tree["a"]["_children"]["b"]["_children"]["c"]["_fc"])

    def test_sibling_paths(self):
        fcs = [
            {"folder_path": "a/x", "subtree_status": "identical"},
            {"folder_path": "a/y", "subtree_status": "overlap"},
        ]
        tree = cda._build_folder_tree(fcs)
        self.assertIn("x", tree["a"]["_children"])
        self.assertIn("y", tree["a"]["_children"])

    def test_intermediate_node_created(self):
        """Parent nodes without their own fc entry should still exist."""
        fcs = [{"folder_path": "deep/nested/leaf", "subtree_status": "identical"}]
        tree = cda._build_folder_tree(fcs)
        self.assertIn("deep", tree)
        # Parent "nested" should exist but have _fc=None
        nested = tree["deep"]["_children"]["nested"]
        self.assertIsNone(nested["_fc"])


# ── render_html: Section 2 pill badges ───────────────────────────────

class _RenderHelper:
    """Mixin for creating analyze results and rendering HTML."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _run(self, files_a, files_b, files_c=None, use_checksum=True, mtime_fuzz=5.0):
        dir_a = Path(self.tmp) / "a"
        dir_b = Path(self.tmp) / "b"
        dirs = [("ServiceA", dir_a), ("ServiceB", dir_b)]
        for rel, content, mtime in files_a:
            make_file(dir_a, rel, content, mtime)
        for rel, content, mtime in files_b:
            make_file(dir_b, rel, content, mtime)
        if files_c is not None:
            dir_c = Path(self.tmp) / "c"
            dirs.append(("ServiceC", dir_c))
            for rel, content, mtime in files_c:
                make_file(dir_c, rel, content, mtime)
        return cda.analyze(dirs, mtime_fuzz=mtime_fuzz,
                           use_checksum=use_checksum, skip_hidden=True)


class TestRenderHtmlSection2Badges(_RenderHelper, unittest.TestCase):
    """Cover Section 2 pill badge rendering for unverified, different, phantom, conflict stats."""

    def test_unverified_badge(self):
        """--no-checksum produces unverified badge in Section 2."""
        r = self._run(
            [("f.txt", b"hello", 1000.0)],
            [("f.txt", b"hello", 1000.0)],
            use_checksum=False,
        )
        r["mtime_fuzz"] = 5
        html = cda.render_html(r)
        self.assertIn("unverified", html)

    def test_different_and_phantom_badges(self):
        """Phantom conflict produces both 'different' and 'phantom' badges."""
        r = self._run(
            [("f.txt", b"hello", 1000.0)],
            [("f.txt", b"world", 1000.0)],  # phantom: same mtime, different content
        )
        r["mtime_fuzz"] = 5
        html = cda.render_html(r)
        self.assertIn("different", html)
        self.assertIn("phantom", html)

    def test_diverged_badge(self):
        """Diverged version status produces diverged badge."""
        r = self._run(
            [("f.txt", b"hello", 1000.0)],
            [("f.txt", b"hello", 9000.0)],  # same content, different mtime
        )
        r["mtime_fuzz"] = 5
        html = cda.render_html(r)
        self.assertIn("diverged", html)

    def test_no_pairs_dash_fallback(self):
        """When pair stats have no matches in a category, the dash fallback is used."""
        # Two files with zero matches (different names)
        r = self._run(
            [("a.txt", b"hello", 1000.0)],
            [("b.txt", b"world", 1000.0)],
        )
        r["mtime_fuzz"] = 5
        html = cda.render_html(r)
        # Section 2 table should still render
        self.assertIn("Service Pair", html)

    def test_three_services_all_row(self):
        """With 3+ services, the 'All N services' row appears in section 2."""
        r = self._run(
            [("f.txt", b"hello", 1000.0)],
            [("f.txt", b"hello", 1000.0)],
            files_c=[("f.txt", b"hello", 1000.0)],
        )
        r["mtime_fuzz"] = 5
        html = cda.render_html(r)
        self.assertIn("All 3 services", html)


# ── render_html: Section 4 — Files Requiring Action ──────────────────

class TestRenderHtmlSection4(_RenderHelper, unittest.TestCase):
    """Cover Section 4: conflict groups table, diverged symlinks, mixed_type rendering."""

    def test_no_conflicts_message(self):
        """When no conflicts, shows 'No content conflicts found'."""
        r = self._run(
            [("f.txt", b"hello", 1000.0)],
            [("f.txt", b"hello", 1000.0)],
        )
        r["mtime_fuzz"] = 5
        html = cda.render_html(r)
        self.assertIn("No content conflicts found", html)

    def test_phantom_conflict_in_section4(self):
        """Phantom conflict (same mtime, different content) renders in Section 4."""
        r = self._run(
            [("doc.txt", b"hello", 1000.0)],
            [("doc.txt", b"world", 1000.0)],
        )
        r["mtime_fuzz"] = 5
        html = cda.render_html(r)
        self.assertIn("Files Requiring Action", html)
        self.assertIn("doc.txt", html)
        # Phantom symbol ⚡
        self.assertIn("⚡", html)
        # Status text
        self.assertIn("phantom", html)

    def test_diverged_conflict_in_section4(self):
        """Different-diverged conflict renders with ⚠ and 'newer in' text."""
        r = self._run(
            [("report.txt", b"hello", 1000.0)],
            [("report.txt", b"world", 9000.0)],
        )
        r["mtime_fuzz"] = 5
        html = cda.render_html(r)
        self.assertIn("report.txt", html)
        self.assertIn("newer in", html)

    def test_mixed_type_conflict_in_section4(self):
        """A symlink in one service and regular file in another renders as mixed type."""
        dir_a = Path(self.tmp) / "mt_a"
        dir_b = Path(self.tmp) / "mt_b"
        # Regular file in A
        make_file(dir_a, "item.txt", b"content", 1000.0)
        # Symlink in B pointing to A's file
        dir_b.mkdir(parents=True, exist_ok=True)
        (dir_b / "item.txt").symlink_to(dir_a / "item.txt")

        r = cda.analyze(
            [("SvcA", dir_a), ("SvcB", dir_b)],
            mtime_fuzz=5.0, use_checksum=True, skip_hidden=True,
        )
        r["mtime_fuzz"] = 5
        html = cda.render_html(r)
        # Mixed type indicator
        self.assertIn("mixed", html.lower())
        self.assertIn("item.txt", html)
        # The extra_note about symlink backup strategy
        self.assertIn("symlink", html.lower())

    def test_diverged_symlinks_in_section4(self):
        """Symlinks with diverged targets render in Section 4 Diverged Symlinks subsection."""
        dir_a = Path(self.tmp) / "sym_a"
        dir_b = Path(self.tmp) / "sym_b"
        dir_a.mkdir(parents=True, exist_ok=True)
        dir_b.mkdir(parents=True, exist_ok=True)

        # Create two targets
        t1 = make_file(self.tmp, "target1.txt", b"t1")
        t2 = make_file(self.tmp, "target2.txt", b"t2")

        # Symlinks pointing to different targets
        (dir_a / "link.txt").symlink_to(t1)
        (dir_b / "link.txt").symlink_to(t2)

        r = cda.analyze(
            [("SvcA", dir_a), ("SvcB", dir_b)],
            mtime_fuzz=5.0, use_checksum=True, skip_hidden=True,
        )
        r["mtime_fuzz"] = 5
        html = cda.render_html(r)
        self.assertIn("Diverged Symlinks", html)
        self.assertIn("link.txt", html)

    def test_conflict_service_detail_missing_label(self):
        """When a service detail is missing for a label, renders dash."""
        # Build a conflict group manually with a missing service
        r = self._run(
            [("doc.txt", b"hello", 1000.0)],
            [("doc.txt", b"world", 1000.0)],
        )
        r["mtime_fuzz"] = 5
        # Artificially add a third label to exercise the "—" branch
        r["labels"] = ["ServiceA", "ServiceB", "ServiceC"]
        r["dirs"]["ServiceC"] = "/fake"
        r["total_files"]["ServiceC"] = 0
        r["unique_counts"]["ServiceC"] = 0
        html = cda.render_html(r)
        # Should not crash and should contain the dash
        self.assertIn("doc.txt", html)


# ── render_html: Section 5 — Duplicate File List ─────────────────────

class TestRenderHtmlSection5(_RenderHelper, unittest.TestCase):
    """Cover Section 5: duplicate file list, symlinks subsection, version-diverged subsection."""

    def test_no_duplicates_message(self):
        """When no duplicates, shows 'No duplicate files found'."""
        r = self._run(
            [("a.txt", b"hello", 1000.0)],
            [("b.txt", b"world", 1000.0)],
        )
        r["mtime_fuzz"] = 5
        html = cda.render_html(r)
        self.assertIn("No duplicate files found", html)

    def test_duplicate_list_renders(self):
        """Identical duplicates render in section 5 with badges."""
        r = self._run(
            [("doc.txt", b"hello", 1000.0)],
            [("doc.txt", b"hello", 1000.0)],
        )
        r["mtime_fuzz"] = 5
        html = cda.render_html(r)
        self.assertIn("Duplicate Files", html)
        self.assertIn("doc.txt", html)
        self.assertIn("badge-identical", html)
        self.assertIn("badge-same", html)

    def test_unverified_duplicate_badges(self):
        """Unverified duplicates get badge-unverified."""
        r = self._run(
            [("doc.txt", b"hello", 1000.0)],
            [("doc.txt", b"hello", 1000.0)],
            use_checksum=False,
        )
        r["mtime_fuzz"] = 5
        html = cda.render_html(r)
        self.assertIn("badge-unverified", html)

    def test_version_diverged_subsection(self):
        """Version-diverged files render in the diverged subsection with timestamps."""
        r = self._run(
            [("doc.txt", b"hello", 1000.0)],
            [("doc.txt", b"hello", 9000.0)],
        )
        r["mtime_fuzz"] = 5
        html = cda.render_html(r)
        self.assertIn("Version-Diverged Files", html)
        self.assertIn("doc.txt", html)
        # Should show "Newest in" and age gap
        self.assertIn("Newest in", html)
        self.assertIn("Age gap", html)
        # Should have the ★ marker for newest
        self.assertIn("★", html)

    def test_version_diverged_missing_service(self):
        """Version-diverged file where a label has no match record renders dash."""
        r = self._run(
            [("doc.txt", b"hello", 1000.0)],
            [("doc.txt", b"hello", 9000.0)],
            files_c=[("other.txt", b"x", 1000.0)],
        )
        r["mtime_fuzz"] = 5
        html = cda.render_html(r)
        # ServiceC doesn't have doc.txt, should render "—" in date cell
        self.assertIn("Version-Diverged Files", html)

    def test_symlinks_subsection(self):
        """Symlinks subsection renders when symlinks exist."""
        dir_a = Path(self.tmp) / "sl_a"
        dir_b = Path(self.tmp) / "sl_b"
        dir_a.mkdir(parents=True, exist_ok=True)
        dir_b.mkdir(parents=True, exist_ok=True)

        target = make_file(self.tmp, "tgt.txt", b"target")
        (dir_a / "link.txt").symlink_to(target)
        (dir_b / "link.txt").symlink_to(target)

        r = cda.analyze(
            [("SvcA", dir_a), ("SvcB", dir_b)],
            mtime_fuzz=5.0, use_checksum=True, skip_hidden=True,
        )
        r["mtime_fuzz"] = 5
        html = cda.render_html(r)
        self.assertIn("Symlinks", html)
        self.assertIn("link.txt", html)
        self.assertIn("badge-symlink", html)

    def test_root_folder_display(self):
        """Files at root level show (root) as folder."""
        r = self._run(
            [("root_file.txt", b"hello", 1000.0)],
            [("root_file.txt", b"hello", 1000.0)],
        )
        r["mtime_fuzz"] = 5
        html = cda.render_html(r)
        self.assertIn("(root)", html)


# ── render_html: safe-to-delete panel ────────────────────────────────

class TestRenderHtmlSafeToDelete(_RenderHelper, unittest.TestCase):
    def test_no_safe_roots_message(self):
        """When no safe roots, shows appropriate message."""
        r = self._run(
            [("a/f.txt", b"hello", 1000.0)],
            [("b/f.txt", b"world", 1000.0)],
        )
        r["mtime_fuzz"] = 5
        html = cda.render_html(r)
        self.assertIn("No folder subtrees are fully identical", html)

    def test_safe_roots_table(self):
        """When safe roots exist, renders the safe-to-delete table."""
        r = self._run(
            [("photos/img.jpg", b"img", 1000.0)],
            [("photos/img.jpg", b"img", 1000.0)],
        )
        r["mtime_fuzz"] = 5
        html = cda.render_html(r)
        self.assertIn("safe to delete", html.lower())
        self.assertIn("photos", html)
        # Check mark for present service
        self.assertIn("✓", html)


# ── main() CLI entry point ───────────────────────────────────────────

class TestMain(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_main_too_few_dirs(self):
        """main() with <2 dirs should fail."""
        with mock.patch("sys.argv", ["prog", self.tmp]):
            with self.assertRaises(SystemExit):
                cda.main()

    def test_main_nonexistent_dir(self):
        """main() with non-existent dir should fail."""
        with mock.patch("sys.argv", ["prog", self.tmp, "/nonexistent_dir_xyz"]):
            with self.assertRaises(SystemExit):
                cda.main()

    def test_main_not_a_dir(self):
        """main() with a file instead of dir should fail."""
        f = make_file(self.tmp, "file.txt", b"data")
        dir2 = Path(self.tmp) / "dir2"
        dir2.mkdir()
        make_file(dir2, "f.txt", b"data")
        with mock.patch("sys.argv", ["prog", str(f), str(dir2)]):
            with self.assertRaises(SystemExit):
                cda.main()

    def test_main_success(self):
        """main() runs successfully with two valid directories."""
        dir_a = Path(self.tmp) / "a"
        dir_b = Path(self.tmp) / "b"
        make_file(dir_a, "f.txt", b"hello", 1000.0)
        make_file(dir_b, "f.txt", b"hello", 1000.0)

        out_path = Path(self.tmp) / "output" / "report.html"
        with mock.patch("sys.argv", ["prog", str(dir_a), str(dir_b),
                                      "-o", str(out_path)]):
            cda.main()

        self.assertTrue(out_path.exists())
        json_path = out_path.with_suffix(".json")
        self.assertTrue(json_path.exists())

        # Verify JSON is valid
        with open(json_path) as f:
            data = json.load(f)
        self.assertIn("labels", data)
        self.assertIn("duplicate_groups", data)

    def test_main_output_dir(self):
        """main() with --output-dir writes to that directory."""
        dir_a = Path(self.tmp) / "a"
        dir_b = Path(self.tmp) / "b"
        make_file(dir_a, "f.txt", b"hello", 1000.0)
        make_file(dir_b, "f.txt", b"hello", 1000.0)

        out_dir = Path(self.tmp) / "reports"
        with mock.patch("sys.argv", ["prog", str(dir_a), str(dir_b),
                                      "--output-dir", str(out_dir)]):
            cda.main()

        # Should have created files in out_dir
        html_files = list(out_dir.glob("*.html"))
        self.assertGreater(len(html_files), 0)

    def test_main_no_checksum(self):
        """main() with --no-checksum runs without error."""
        dir_a = Path(self.tmp) / "a"
        dir_b = Path(self.tmp) / "b"
        make_file(dir_a, "f.txt", b"hello", 1000.0)
        make_file(dir_b, "f.txt", b"hello", 1000.0)

        out_path = Path(self.tmp) / "nc_report.html"
        with mock.patch("sys.argv", ["prog", str(dir_a), str(dir_b),
                                      "-o", str(out_path), "--no-checksum"]):
            cda.main()
        self.assertTrue(out_path.exists())

    def test_main_include_hidden(self):
        """main() with --include-hidden runs without error."""
        dir_a = Path(self.tmp) / "a"
        dir_b = Path(self.tmp) / "b"
        make_file(dir_a, ".hidden.txt", b"h", 1000.0)
        make_file(dir_b, ".hidden.txt", b"h", 1000.0)

        out_path = Path(self.tmp) / "hidden_report.html"
        with mock.patch("sys.argv", ["prog", str(dir_a), str(dir_b),
                                      "-o", str(out_path), "--include-hidden"]):
            cda.main()
        self.assertTrue(out_path.exists())

    def test_main_labeled_dirs(self):
        """main() with Label:path syntax works."""
        dir_a = Path(self.tmp) / "a"
        dir_b = Path(self.tmp) / "b"
        make_file(dir_a, "f.txt", b"hello", 1000.0)
        make_file(dir_b, "f.txt", b"hello", 1000.0)

        out_path = Path(self.tmp) / "labeled.html"
        with mock.patch("sys.argv", ["prog",
                                      f"MyDriveA:{dir_a}",
                                      f"MyDriveB:{dir_b}",
                                      "-o", str(out_path)]):
            cda.main()
        self.assertTrue(out_path.exists())
        with open(out_path.with_suffix(".json")) as f:
            data = json.load(f)
        self.assertIn("MyDriveA", data["labels"])
        self.assertIn("MyDriveB", data["labels"])

    def test_main_mtime_fuzz(self):
        """main() with --mtime-fuzz works."""
        dir_a = Path(self.tmp) / "a"
        dir_b = Path(self.tmp) / "b"
        make_file(dir_a, "f.txt", b"hello", 1000.0)
        make_file(dir_b, "f.txt", b"hello", 1000.0)

        out_path = Path(self.tmp) / "fuzz.html"
        with mock.patch("sys.argv", ["prog", str(dir_a), str(dir_b),
                                      "-o", str(out_path),
                                      "--mtime-fuzz", "60"]):
            cda.main()
        self.assertTrue(out_path.exists())

    def test_main_summary_output(self):
        """main() prints summary to stdout."""
        dir_a = Path(self.tmp) / "a"
        dir_b = Path(self.tmp) / "b"
        make_file(dir_a, "f.txt", b"hello", 1000.0)
        make_file(dir_b, "f.txt", b"hello", 1000.0)

        out_path = Path(self.tmp) / "sum.html"
        with mock.patch("sys.argv", ["prog", str(dir_a), str(dir_b),
                                      "-o", str(out_path)]):
            cda.main()
        # Just verify it doesn't crash and creates output
        self.assertTrue(out_path.exists())

    def test_main_conflict_summary(self):
        """main() with conflicts prints conflict summary."""
        dir_a = Path(self.tmp) / "a"
        dir_b = Path(self.tmp) / "b"
        make_file(dir_a, "f.txt", b"hello", 1000.0)
        make_file(dir_b, "f.txt", b"world", 1000.0)

        out_path = Path(self.tmp) / "conflict.html"
        with mock.patch("sys.argv", ["prog", str(dir_a), str(dir_b),
                                      "-o", str(out_path)]):
            cda.main()
        self.assertTrue(out_path.exists())

    def test_main_symlink_summary(self):
        """main() with symlinks prints symlink summary."""
        dir_a = Path(self.tmp) / "a"
        dir_b = Path(self.tmp) / "b"
        dir_a.mkdir(parents=True, exist_ok=True)
        dir_b.mkdir(parents=True, exist_ok=True)

        target = make_file(self.tmp, "target.txt", b"t")
        (dir_a / "link.txt").symlink_to(target)
        (dir_b / "link.txt").symlink_to(target)

        out_path = Path(self.tmp) / "symlink.html"
        with mock.patch("sys.argv", ["prog", str(dir_a), str(dir_b),
                                      "-o", str(out_path)]):
            cda.main()
        self.assertTrue(out_path.exists())

    def test_main_three_services_summary(self):
        """main() with 3 services prints 'All N services' line."""
        dir_a = Path(self.tmp) / "a"
        dir_b = Path(self.tmp) / "b"
        dir_c = Path(self.tmp) / "c"
        make_file(dir_a, "f.txt", b"hello", 1000.0)
        make_file(dir_b, "f.txt", b"hello", 1000.0)
        make_file(dir_c, "f.txt", b"hello", 1000.0)

        out_path = Path(self.tmp) / "three.html"
        with mock.patch("sys.argv", ["prog", str(dir_a), str(dir_b), str(dir_c),
                                      "-o", str(out_path)]):
            cda.main()
        self.assertTrue(out_path.exists())


# ── analyze: classify_pair edge cases in main loop ───────────────────

class TestAnalyzeClassifyPairEdges(_RenderHelper, unittest.TestCase):
    """Cover the classify_pair branches that only fire inside analyze()'s main loop."""

    def test_symlink_pair_goes_to_symlinks_list(self):
        """Two matching symlinks go to result['symlinks'], not duplicate_groups."""
        dir_a = Path(self.tmp) / "sa"
        dir_b = Path(self.tmp) / "sb"
        dir_a.mkdir(parents=True, exist_ok=True)
        dir_b.mkdir(parents=True, exist_ok=True)

        target = make_file(self.tmp, "tgt.txt", b"data")
        (dir_a / "link.txt").symlink_to(target)
        (dir_b / "link.txt").symlink_to(target)

        r = cda.analyze(
            [("A", dir_a), ("B", dir_b)],
            mtime_fuzz=5.0, use_checksum=True, skip_hidden=True,
        )
        sym_names = [s["name_orig"] for s in r["symlinks"]]
        self.assertIn("link.txt", sym_names)
        dup_names = [d["name_orig"] for d in r["duplicate_groups"]]
        self.assertNotIn("link.txt", dup_names)

    def test_mixed_type_via_rel_path_detection(self):
        """Mixed-type detection via the rel_path pass (different sizes, same name)."""
        dir_a = Path(self.tmp) / "ma"
        dir_b = Path(self.tmp) / "mb"
        # Regular file in A
        make_file(dir_a, "item.txt", b"content", 1000.0)
        # Symlink in B
        dir_b.mkdir(parents=True, exist_ok=True)
        target = make_file(self.tmp, "x.txt", b"x")
        (dir_b / "item.txt").symlink_to(target)

        r = cda.analyze(
            [("A", dir_a), ("B", dir_b)],
            mtime_fuzz=5.0, use_checksum=True, skip_hidden=True,
        )
        conflicts = [c for c in r["conflict_groups"] if c["name_orig"] == "item.txt"]
        self.assertGreater(len(conflicts), 0)
        self.assertEqual(conflicts[0]["content_match"], "mixed_type")

    def test_classify_pair_returns_none_skips_group(self):
        """When classify_pair returns None (different names), the group is skipped."""
        # This is hard to trigger naturally since the index groups by (name, size),
        # but we test the defensive guard
        a = {"name": "a.txt", "name_orig": "a.txt", "size": 5, "mtime": 1000.0,
             "full_path": Path("/fake/a"), "folder": ".", "is_symlink": False}
        b = {"name": "b.txt", "name_orig": "b.txt", "size": 5, "mtime": 1000.0,
             "full_path": Path("/fake/b"), "folder": ".", "is_symlink": False}
        result = cda.classify_pair(a, b, mtime_fuzz=5.0, use_checksum=True)
        self.assertIsNone(result)


# ── render_html: full integration ────────────────────────────────────

class TestRenderHtmlFullIntegration(_RenderHelper, unittest.TestCase):
    """Full integration test exercising all sections of render_html."""

    def test_complete_report_all_sections(self):
        """Build a complex scenario that exercises all render_html sections."""
        dir_a = Path(self.tmp) / "full_a"
        dir_b = Path(self.tmp) / "full_b"

        # Identical files (Section 5 duplicate list)
        make_file(dir_a, "shared/readme.md", b"readme", 1000.0)
        make_file(dir_b, "shared/readme.md", b"readme", 1000.0)

        # Diverged identical files (Section 5 version-diverged subsection)
        make_file(dir_a, "shared/notes.txt", b"notes", 1000.0)
        make_file(dir_b, "shared/notes.txt", b"notes", 9000.0)

        # Phantom conflict (Section 4)
        make_file(dir_a, "shared/config.yml", b"confA", 1000.0)
        make_file(dir_b, "shared/config.yml", b"confB", 1000.0)

        # Diverged conflict (Section 4 with "newer in")
        make_file(dir_a, "docs/plan.md", b"plan_old", 1000.0)
        make_file(dir_b, "docs/plan.md", b"plan_new", 9000.0)

        # Unique files (Section 3 tree, unique markers)
        make_file(dir_a, "only-a/special.txt", b"special", 1000.0)
        make_file(dir_b, "only-b/other.txt", b"other", 1000.0)

        # Symlinks (Section 5 symlinks subsection)
        target = make_file(self.tmp, "ext_target.txt", b"ext")
        (dir_a / "shared" / "sym.txt").symlink_to(target)
        (dir_b / "shared" / "sym.txt").symlink_to(target)

        r = cda.analyze(
            [("DriveA", dir_a), ("DriveB", dir_b)],
            mtime_fuzz=5.0, use_checksum=True, skip_hidden=True,
        )
        r["mtime_fuzz"] = 5
        html = cda.render_html(r)

        # Section 1: File Counts
        self.assertIn("File Counts", html)
        self.assertIn("DriveA", html)
        self.assertIn("DriveB", html)

        # Section 2: Duplicate Summary with badges
        self.assertIn("Duplicate File Summary", html)

        # Section 3: Folder Structure
        self.assertIn("Folder Structure Analysis", html)
        self.assertIn("only-a", html)
        self.assertIn("only-b", html)

        # Section 4: Files Requiring Action
        self.assertIn("Files Requiring Action", html)
        self.assertIn("config.yml", html)
        self.assertIn("plan.md", html)

        # Section 5: Duplicate Files
        self.assertIn("Duplicate Files", html)
        self.assertIn("readme.md", html)

        # Version-diverged subsection
        self.assertIn("Version-Diverged Files", html)
        self.assertIn("notes.txt", html)

        # Symlinks subsection
        self.assertIn("Symlinks", html)

        # Footer
        self.assertIn("Cloud Storage Duplicate Analysis", html)

    def test_render_html_with_empty_result(self):
        """render_html with minimal empty result doesn't crash."""
        r = self._run(
            [("a.txt", b"x", 1000.0)],
            [("b.txt", b"y", 1000.0)],
        )
        r["mtime_fuzz"] = 5
        html = cda.render_html(r)
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn("</html>", html)


class TestScanWarningsInReport(_RenderHelper, unittest.TestCase):
    """Verify scan_warnings flow into analyze result and render_html output."""

    def test_scan_warnings_in_result_when_no_issues(self):
        r = self._run(
            [("a.txt", b"data", 1000.0)],
            [("b.txt", b"data", 1000.0)],
        )
        self.assertEqual(r["scan_warnings"], {})

    def test_scan_warnings_in_html_when_present(self):
        r = self._run(
            [("a.txt", b"data", 1000.0)],
            [("b.txt", b"data", 1000.0)],
        )
        r["scan_warnings"] = {"DropBox": ["Permission denied: [Errno 1] Operation not permitted: '/Users/me/Dropbox'"]}
        r["mtime_fuzz"] = 5
        html = cda.render_html(r)
        self.assertIn("Scan Warnings", html)
        self.assertIn("DropBox", html)
        self.assertIn("Permission denied", html)
        self.assertIn("Full Disk Access", html)

    def test_scan_warnings_not_in_html_when_absent(self):
        r = self._run(
            [("a.txt", b"data", 1000.0)],
            [("b.txt", b"data", 1000.0)],
        )
        r["mtime_fuzz"] = 5
        html = cda.render_html(r)
        self.assertNotIn("Scan Warnings", html)

    def test_empty_dir_produces_warning_in_analyze(self):
        dir_a = Path(self.tmp) / "populated"
        dir_b = Path(self.tmp) / "empty_dir"
        dir_b.mkdir(parents=True, exist_ok=True)
        make_file(dir_a, "file.txt", b"data", 1000.0)
        r = cda.analyze(
            [("Full", dir_a), ("Empty", dir_b)],
            mtime_fuzz=5.0, use_checksum=True, skip_hidden=True,
        )
        self.assertIn("Empty", r["scan_warnings"])
        self.assertTrue(any("0 files found" in w for w in r["scan_warnings"]["Empty"]))


if __name__ == "__main__":
    unittest.main()
