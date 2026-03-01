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
        # This guard is dead code at the real call site (caller always passes records
        # from the same (name, size) index key), but is retained as a defensive check.
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

    def test_hash_failure_falls_back_to_mtime(self):
        """If MD5 cannot be read (e.g. file deleted after stat), falls back to unverified."""
        a = self._rec("f.txt", b"hello", mtime=1000.0, subdir="a")
        b = self._rec("f.txt", b"hello", mtime=1000.0, subdir="b")
        # Point one record at a non-existent path to trigger hash failure
        a = dict(a, full_path=Path(self.tmp) / "a" / "nonexistent.txt")
        result = cda.classify_pair(a, b, mtime_fuzz=5.0, use_checksum=True)
        self.assertEqual(result, ("unverified", "same"))

    def test_no_checksum_empty_file_still_identical(self):
        """Empty files are always identical/same, even with use_checksum=False."""
        a = self._rec("empty.txt", b"", mtime=1000.0, subdir="a")
        b = self._rec("empty.txt", b"", mtime=9000.0, subdir="b")
        result = cda.classify_pair(a, b, mtime_fuzz=5.0, use_checksum=False)
        self.assertEqual(result, ("identical", "same"))


class TestSubtreeRollup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil; shutil.rmtree(self.tmp)

    def _run_tree(self, structure_a, structure_b):
        """structure: list of (rel_path, content_bytes, mtime)"""
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
        self.assertIn("photos", fc_by_path)
        self.assertEqual(fc_by_path["photos"]["subtree_status"], "identical")
        safe_paths = [s["folder_path"] for s in r["safe_to_delete_roots"]]
        self.assertIn("photos", safe_paths)

    def test_partial_subtree_not_in_safe_roots(self):
        r = self._run_tree(
            [("docs/work/a.txt",      b"aaa", 1000.0),
             ("docs/personal/b.txt",  b"bbb", 1000.0)],
            [("docs/work/a.txt",      b"aaa", 1000.0),
             ("docs/personal/c.txt",  b"ccc", 1000.0)],  # different file in personal
        )
        safe_paths = [s["folder_path"] for s in r["safe_to_delete_roots"]]
        self.assertNotIn("docs", safe_paths)

    def test_safe_root_is_highest_level_only(self):
        """When photos/ is fully identical, photos/2020/ should NOT also appear in safe_roots."""
        r = self._run_tree(
            [("photos/2020/jan.jpg", b"img1", 1000.0)],
            [("photos/2020/jan.jpg", b"img1", 1000.0)],
        )
        safe_paths = [s["folder_path"] for s in r["safe_to_delete_roots"]]
        self.assertIn("photos", safe_paths)
        self.assertNotIn("photos/2020", safe_paths)

    def test_subtree_total_files_counts_all_descendants(self):
        r = self._run_tree(
            [("photos/2020/jan.jpg", b"img1", 1000.0),
             ("photos/2020/feb.jpg", b"img2", 1000.0),
             ("photos/2021/mar.jpg", b"img3", 1000.0)],
            [("photos/2020/jan.jpg", b"img1", 1000.0),
             ("photos/2020/feb.jpg", b"img2", 1000.0),
             ("photos/2021/mar.jpg", b"img3", 1000.0)],
        )
        fc_by_path = {fc["folder_path"]: fc for fc in r["folder_comparisons"]}
        # photos/ has 0 files directly; 2 in 2020/ and 1 in 2021/ = 3 total in subtree
        self.assertEqual(fc_by_path["photos"]["subtree_total_files"], 3)


class TestAnalyzeIntegration(unittest.TestCase):
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


class TestSymlinkDetection(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil; shutil.rmtree(self.tmp)

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
        # size must be -1 (sentinel) — must not be 0 to avoid colliding with real empty files
        assert symlink_record.get("size") == -1
        # symlink_target must be a str (not a Path object), for JSON serialisation safety
        assert symlink_record.get("symlink_target") is not None
        assert isinstance(symlink_record.get("symlink_target"), str)
        assert str(regular_file) in symlink_record.get("symlink_target")

    def test_dangling_symlink_detection(self):
        """Verify dangling symlinks (target doesn't exist) are handled gracefully."""
        # Create a symlink pointing to a non-existent target
        symlink_path = Path(self.tmp) / "scan_dir" / "dangling.txt"
        symlink_path.parent.mkdir(parents=True, exist_ok=True)
        symlink_path.symlink_to(Path(self.tmp) / "nonexistent_target.txt")

        # Must not crash
        records = cda.scan_directory(Path(self.tmp) / "scan_dir", skip_hidden=False)

        dangling = next((r for r in records if r["name_orig"] == "dangling.txt"), None)
        assert dangling is not None
        assert dangling.get("is_symlink") == True
        assert dangling.get("size") == -1
        # symlink_target is either None (truly unresolvable) or a str (macOS resolve() may
        # return an absolute path even for a dangling symlink — both outcomes are acceptable)
        target = dangling.get("symlink_target")
        assert target is None or isinstance(target, str)

    def test_regular_file_is_symlink_false(self):
        """Verify regular files have is_symlink=False."""
        make_file(self.tmp, "a/regular.txt", b"content")

        records = cda.scan_directory(Path(self.tmp) / "a", skip_hidden=False)

        regular_record = next((r for r in records if r["name_orig"] == "regular.txt"), None)

        assert regular_record is not None
        assert regular_record.get("is_symlink") == False

    def test_symlink_identical_targets(self):
        """Two symlinks pointing to same target are identical."""
        target = make_file(self.tmp, "target/file.txt", b"target content")

        rec_a = {
            "name": "link.txt", "name_orig": "link.txt",
            "is_symlink": True, "symlink_target": str(target),
            "folder": "a", "size": -1, "mtime": 0.0
        }
        rec_b = {
            "name": "link.txt", "name_orig": "link.txt",
            "is_symlink": True, "symlink_target": str(target),
            "folder": "b", "size": -1, "mtime": 0.0
        }

        result = cda.classify_pair(rec_a, rec_b, mtime_fuzz=5.0, use_checksum=True)
        self.assertEqual(result, ("symlink", "target_identical"))

    def test_symlink_diverged_targets(self):
        """Two symlinks pointing to different targets diverge."""
        rec_a = {
            "name": "link.txt", "name_orig": "link.txt",
            "is_symlink": True, "symlink_target": "/path/to/target1",
            "folder": "a", "size": -1, "mtime": 0.0
        }
        rec_b = {
            "name": "link.txt", "name_orig": "link.txt",
            "is_symlink": True, "symlink_target": "/path/to/target2",
            "folder": "b", "size": -1, "mtime": 0.0
        }

        result = cda.classify_pair(rec_a, rec_b, mtime_fuzz=5.0, use_checksum=True)
        self.assertEqual(result, ("symlink", "target_diverged"))

    def test_symlink_vs_file_conflict(self):
        """Symlink in one service, regular file in another = conflict."""
        rec_symlink = {
            "name": "item.txt", "name_orig": "item.txt",
            "is_symlink": True, "symlink_target": "/path/to/target",
            "folder": "a", "size": -1, "mtime": 0.0
        }
        rec_file = {
            "name": "item.txt", "name_orig": "item.txt",
            "is_symlink": False, "size": 100, "mtime": 1000.0,
            "folder": "b"
        }

        result = cda.classify_pair(rec_symlink, rec_file, mtime_fuzz=5.0, use_checksum=True)
        self.assertEqual(result, ("mixed_type", "conflict"))


if __name__ == "__main__":
    unittest.main()
