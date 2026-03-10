"""Tests for single-service (unique) folder visibility in folder_comparisons and render_html.

These tests cover the bug where folders/files that exist in only one service
are silently omitted from folder_comparisons and thus invisible in the
Section 3 folder tree.

Issue: Folders/files unique to a single service are invisible in folder tree (Section 3)
"""
import sys, os, tempfile, unittest
from pathlib import Path
from collections import defaultdict

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


class _AnalyzeHelper:
    """Mixin providing helpers for multi-service analyze() tests."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp)

    def _make_dirs(self, *structures):
        """
        Create N service directories from structure specs.
        Each structure: list of (rel_path, content_bytes, mtime).
        Returns list of (label, Path) tuples suitable for analyze().
        """
        dirs = []
        for i, structure in enumerate(structures):
            label = chr(ord("A") + i)
            d = Path(self.tmp) / label.lower()
            for rel, content, mtime in structure:
                make_file(d, rel, content, mtime)
            dirs.append((label, d))
        return dirs

    def _run(self, *structures, use_checksum=True, mtime_fuzz=5.0):
        """Run analyze() with N services. Each arg is a structure list."""
        dirs = self._make_dirs(*structures)
        return cda.analyze(dirs, mtime_fuzz=mtime_fuzz,
                           use_checksum=use_checksum, skip_hidden=True)

    def _fc_paths(self, result):
        """Return set of folder_path values from folder_comparisons."""
        return {fc["folder_path"] for fc in result["folder_comparisons"]}

    def _fc_by_path(self, result):
        """Return dict of folder_path → fc entry."""
        return {fc["folder_path"]: fc for fc in result["folder_comparisons"]}


# ─────────────────────────────────────────────────────────────────────
# Scenario 1: Folder unique to one service at root level (3 services)
# ─────────────────────────────────────────────────────────────────────

class TestUniqueRootFolder3Services(_AnalyzeHelper, unittest.TestCase):
    """A: docs/readme.md, B: docs/readme.md + photos/vacation/beach.jpg, C: docs/readme.md
    The photos/ tree exists only in B and must appear in folder_comparisons."""

    def _result(self):
        return self._run(
            [("docs/readme.md", b"readme", 1000.0)],
            [("docs/readme.md", b"readme", 1000.0),
             ("photos/vacation/beach.jpg", b"beachimg", 1000.0)],
            [("docs/readme.md", b"readme", 1000.0)],
        )

    def test_unique_folder_in_folder_comparisons(self):
        """photos/ and photos/vacation/ must appear in folder_comparisons."""
        r = self._result()
        paths = self._fc_paths(r)
        self.assertIn("photos", paths)
        self.assertIn("photos/vacation", paths)

    def test_unique_folder_relationship_is_unique(self):
        """Folders present in only one service should have relationship='unique'."""
        r = self._result()
        fc_map = self._fc_by_path(r)
        self.assertEqual(fc_map["photos/vacation"]["relationship"], "unique")

    def test_unique_folder_services_present(self):
        """The unique folder should list only the service that has it."""
        r = self._result()
        fc_map = self._fc_by_path(r)
        self.assertEqual(fc_map["photos/vacation"]["services_present"], ["B"])

    def test_unique_folder_has_file_count(self):
        """total_unique_files should count files directly in the folder."""
        r = self._result()
        fc_map = self._fc_by_path(r)
        self.assertEqual(fc_map["photos/vacation"]["total_unique_files"], 1)

    def test_unique_folder_in_html_tree(self):
        """render_html() must include the unique folder in Section 3 tree."""
        r = self._result()
        html = cda.render_html(r)
        self.assertIn("photos", html)
        self.assertIn("vacation", html)

    def test_unique_file_counted_correctly(self):
        """beach.jpg should be in B's unique_counts."""
        r = self._result()
        self.assertGreaterEqual(r["unique_counts"]["B"], 1)


# ─────────────────────────────────────────────────────────────────────
# Scenario 2: Subfolder unique under shared parent (3 services)
# ─────────────────────────────────────────────────────────────────────

class TestUniqueSubfolderUnderSharedParent(_AnalyzeHelper, unittest.TestCase):
    """A,C: projects/web/index.html. B: same + projects/mobile/app.js.
    projects/mobile/ is unique to B but sits under the shared projects/ node."""

    def _result(self):
        return self._run(
            [("projects/web/index.html", b"html", 1000.0)],
            [("projects/web/index.html", b"html", 1000.0),
             ("projects/mobile/app.js",  b"js",   1000.0)],
            [("projects/web/index.html", b"html", 1000.0)],
        )

    def test_unique_subfolder_in_folder_comparisons(self):
        """projects/mobile/ must appear in folder_comparisons."""
        r = self._result()
        paths = self._fc_paths(r)
        self.assertIn("projects/mobile", paths)

    def test_shared_parent_still_present(self):
        """projects/ and projects/web/ must still appear (shared across all)."""
        r = self._result()
        paths = self._fc_paths(r)
        self.assertIn("projects", paths)
        self.assertIn("projects/web", paths)

    def test_unique_subfolder_relationship(self):
        r = self._result()
        fc_map = self._fc_by_path(r)
        self.assertEqual(fc_map["projects/mobile"]["relationship"], "unique")

    def test_parent_subtree_not_identical(self):
        """projects/ subtree should NOT be 'identical' since B has an extra subfolder."""
        r = self._result()
        fc_map = self._fc_by_path(r)
        self.assertNotEqual(fc_map["projects"]["subtree_status"], "identical")

    def test_unique_subfolder_in_html_tree(self):
        r = self._result()
        html = cda.render_html(r)
        self.assertIn("mobile", html)


# ─────────────────────────────────────────────────────────────────────
# Scenario 3: Deep nesting — unique folder several levels down
# ─────────────────────────────────────────────────────────────────────

class TestDeepNestedUniqueFolder(_AnalyzeHelper, unittest.TestCase):
    """A,C: a/b/c/shared.txt. B: same + a/b/c/d/e/deep.txt.
    a/b/c/d/ and a/b/c/d/e/ are unique to B."""

    def _result(self):
        return self._run(
            [("a/b/c/shared.txt", b"shared", 1000.0)],
            [("a/b/c/shared.txt", b"shared", 1000.0),
             ("a/b/c/d/e/deep.txt", b"deep", 1000.0)],
            [("a/b/c/shared.txt", b"shared", 1000.0)],
        )

    def test_deep_unique_folders_in_comparisons(self):
        """Both a/b/c/d/ and a/b/c/d/e/ must appear."""
        r = self._result()
        paths = self._fc_paths(r)
        self.assertIn("a/b/c/d", paths)
        self.assertIn("a/b/c/d/e", paths)

    def test_shared_ancestors_present(self):
        r = self._result()
        paths = self._fc_paths(r)
        for p in ["a", "a/b", "a/b/c"]:
            self.assertIn(p, paths, f"{p} should be in folder_comparisons")

    def test_deep_unique_relationship(self):
        r = self._result()
        fc_map = self._fc_by_path(r)
        self.assertEqual(fc_map["a/b/c/d"]["relationship"], "unique")
        self.assertEqual(fc_map["a/b/c/d/e"]["relationship"], "unique")

    def test_deep_unique_services_present(self):
        r = self._result()
        fc_map = self._fc_by_path(r)
        self.assertEqual(fc_map["a/b/c/d/e"]["services_present"], ["B"])

    def test_subtree_total_files_includes_unique(self):
        """a/b/c/d/ subtree should count 1 file (deep.txt)."""
        r = self._result()
        fc_map = self._fc_by_path(r)
        self.assertEqual(fc_map["a/b/c/d"]["subtree_total_files"], 1)


# ─────────────────────────────────────────────────────────────────────
# Scenario 4: Multiple services each have unique folders (2 services)
# ─────────────────────────────────────────────────────────────────────

class TestMultipleServicesUniquefolders(_AnalyzeHelper, unittest.TestCase):
    """A: shared/file.txt + only-a/special.txt.
    B: shared/file.txt + only-b/other.txt.
    Both only-a/ and only-b/ should appear."""

    def _result(self):
        return self._run(
            [("shared/file.txt",  b"shared", 1000.0),
             ("only-a/special.txt", b"aonly", 1000.0)],
            [("shared/file.txt",  b"shared", 1000.0),
             ("only-b/other.txt", b"bonly",  1000.0)],
        )

    def test_both_unique_folders_in_comparisons(self):
        r = self._result()
        paths = self._fc_paths(r)
        self.assertIn("only-a", paths)
        self.assertIn("only-b", paths)

    def test_unique_folders_have_correct_service(self):
        r = self._result()
        fc_map = self._fc_by_path(r)
        self.assertEqual(fc_map["only-a"]["services_present"], ["A"])
        self.assertEqual(fc_map["only-b"]["services_present"], ["B"])

    def test_unique_folders_relationship(self):
        r = self._result()
        fc_map = self._fc_by_path(r)
        self.assertEqual(fc_map["only-a"]["relationship"], "unique")
        self.assertEqual(fc_map["only-b"]["relationship"], "unique")

    def test_shared_folder_still_works(self):
        r = self._result()
        fc_map = self._fc_by_path(r)
        self.assertIn("shared", fc_map)
        self.assertEqual(fc_map["shared"]["relationship"], "identical")


# ─────────────────────────────────────────────────────────────────────
# Scenario 5: Unique folder at root level (2 services, simplest case)
# ─────────────────────────────────────────────────────────────────────

class TestUniqueRootFolder2Services(_AnalyzeHelper, unittest.TestCase):
    """A: docs/readme.md. B: photos/pic.jpg.
    No shared folders at all — both should still appear."""

    def _result(self):
        return self._run(
            [("docs/readme.md",  b"readme", 1000.0)],
            [("photos/pic.jpg",  b"picdata", 1000.0)],
        )

    def test_both_unique_folders_present(self):
        r = self._result()
        paths = self._fc_paths(r)
        self.assertIn("docs", paths)
        self.assertIn("photos", paths)

    def test_no_duplicates(self):
        r = self._result()
        self.assertEqual(len(r["duplicate_groups"]), 0)

    def test_unique_counts_correct(self):
        r = self._result()
        self.assertEqual(r["unique_counts"]["A"], 1)
        self.assertEqual(r["unique_counts"]["B"], 1)


# ─────────────────────────────────────────────────────────────────────
# Scenario 6: Extra files in shared folder (not a unique folder,
# but files unique to one service within a shared folder)
# ─────────────────────────────────────────────────────────────────────

class TestExtraFilesInSharedFolder(_AnalyzeHelper, unittest.TestCase):
    """A: docs/readme.md. B: docs/readme.md + docs/extra.txt.
    docs/ is shared but extra.txt is unique to B — this case already
    works, but verify it alongside the unique folder fix."""

    def _result(self):
        return self._run(
            [("docs/readme.md",  b"readme", 1000.0)],
            [("docs/readme.md",  b"readme", 1000.0),
             ("docs/extra.txt",  b"extra",  1000.0)],
        )

    def test_shared_folder_relationship_is_subset(self):
        r = self._result()
        fc_map = self._fc_by_path(r)
        self.assertIn(fc_map["docs"]["relationship"], ("subset/superset", "overlap"))

    def test_extra_file_counted_as_unique(self):
        r = self._result()
        self.assertGreaterEqual(r["unique_counts"]["B"], 1)


# ─────────────────────────────────────────────────────────────────────
# Scenario 7: Unique folder with multiple files (2 services)
# ─────────────────────────────────────────────────────────────────────

class TestUniqueFolderMultipleFiles(_AnalyzeHelper, unittest.TestCase):
    """A: shared/f.txt. B: shared/f.txt + extras/a.txt + extras/b.txt + extras/c.txt.
    extras/ is unique to B with 3 files — verify count."""

    def _result(self):
        return self._run(
            [("shared/f.txt", b"shared", 1000.0)],
            [("shared/f.txt", b"shared", 1000.0),
             ("extras/a.txt", b"aaa",    1000.0),
             ("extras/b.txt", b"bbb",    1000.0),
             ("extras/c.txt", b"ccc",    1000.0)],
        )

    def test_unique_folder_in_comparisons(self):
        r = self._result()
        self.assertIn("extras", self._fc_paths(r))

    def test_unique_folder_file_count(self):
        r = self._result()
        fc_map = self._fc_by_path(r)
        self.assertEqual(fc_map["extras"]["total_unique_files"], 3)

    def test_unique_folder_subtree_total(self):
        r = self._result()
        fc_map = self._fc_by_path(r)
        self.assertEqual(fc_map["extras"]["subtree_total_files"], 3)


# ─────────────────────────────────────────────────────────────────────
# Scenario 8: Unique nested tree (multi-level, single service)
# ─────────────────────────────────────────────────────────────────────

class TestUniqueNestedTree(_AnalyzeHelper, unittest.TestCase):
    """A: shared/f.txt. B: shared/f.txt + archive/2020/jan.txt + archive/2021/feb.txt.
    Entire archive/ tree is unique to B."""

    def _result(self):
        return self._run(
            [("shared/f.txt", b"shared", 1000.0)],
            [("shared/f.txt", b"shared", 1000.0),
             ("archive/2020/jan.txt", b"jan", 1000.0),
             ("archive/2021/feb.txt", b"feb", 1000.0)],
        )

    def test_all_unique_folders_present(self):
        r = self._result()
        paths = self._fc_paths(r)
        self.assertIn("archive", paths)
        self.assertIn("archive/2020", paths)
        self.assertIn("archive/2021", paths)

    def test_archive_subtree_total(self):
        r = self._result()
        fc_map = self._fc_by_path(r)
        self.assertEqual(fc_map["archive"]["subtree_total_files"], 2)

    def test_leaf_folder_file_counts(self):
        r = self._result()
        fc_map = self._fc_by_path(r)
        self.assertEqual(fc_map["archive/2020"]["total_unique_files"], 1)
        self.assertEqual(fc_map["archive/2021"]["total_unique_files"], 1)


# ─────────────────────────────────────────────────────────────────────
# Scenario 9: 3 services, each has a unique folder
# ─────────────────────────────────────────────────────────────────────

class TestThreeServicesEachUniqueFolder(_AnalyzeHelper, unittest.TestCase):
    """A: shared/f.txt + only-a/a.txt.
    B: shared/f.txt + only-b/b.txt.
    C: shared/f.txt + only-c/c.txt."""

    def _result(self):
        return self._run(
            [("shared/f.txt",   b"shared", 1000.0),
             ("only-a/a.txt",   b"aaa",    1000.0)],
            [("shared/f.txt",   b"shared", 1000.0),
             ("only-b/b.txt",   b"bbb",    1000.0)],
            [("shared/f.txt",   b"shared", 1000.0),
             ("only-c/c.txt",   b"ccc",    1000.0)],
        )

    def test_all_three_unique_folders_present(self):
        r = self._result()
        paths = self._fc_paths(r)
        self.assertIn("only-a", paths)
        self.assertIn("only-b", paths)
        self.assertIn("only-c", paths)

    def test_each_unique_folder_correct_service(self):
        r = self._result()
        fc_map = self._fc_by_path(r)
        self.assertEqual(fc_map["only-a"]["services_present"], ["A"])
        self.assertEqual(fc_map["only-b"]["services_present"], ["B"])
        self.assertEqual(fc_map["only-c"]["services_present"], ["C"])

    def test_all_relationships_unique(self):
        r = self._result()
        fc_map = self._fc_by_path(r)
        for name in ["only-a", "only-b", "only-c"]:
            self.assertEqual(fc_map[name]["relationship"], "unique")


# ─────────────────────────────────────────────────────────────────────
# Scenario 10: Render HTML — unique folders must appear in tree
# ─────────────────────────────────────────────────────────────────────

class TestRenderHtmlUniquefolders(_AnalyzeHelper, unittest.TestCase):
    """Verify that render_html() outputs unique folder nodes in Section 3."""

    def _result(self):
        return self._run(
            [("shared/f.txt",  b"data", 1000.0)],
            [("shared/f.txt",  b"data", 1000.0),
             ("backup/old/legacy.txt", b"legacy", 1000.0)],
        )

    def test_unique_folder_name_in_html(self):
        r = self._result()
        html = cda.render_html(r)
        self.assertIn("backup", html)
        self.assertIn("old", html)

    def test_unique_diamond_marker_in_html(self):
        """Unique-to-one-service files should have the ◆ diamond marker."""
        r = self._result()
        html = cda.render_html(r)
        # ◆ is &#9670; or the literal character
        self.assertTrue(
            "&#9670;" in html or "\u25c6" in html,
            "Diamond marker for unique files not found in HTML"
        )

    def test_only_in_label_text_in_html(self):
        """Section 3 tree should show 'Only in B' for unique files."""
        r = self._result()
        html = cda.render_html(r)
        self.assertIn("Only in B", html)


# ─────────────────────────────────────────────────────────────────────
# Scenario 11: Unique folder does NOT break safe-to-delete logic
# ─────────────────────────────────────────────────────────────────────

class TestUniqueFolderSafeToDelete(_AnalyzeHelper, unittest.TestCase):
    """A unique folder should never be in safe_to_delete_roots
    (since it's not duplicated, deleting it would lose data)."""

    def _result(self):
        return self._run(
            [("shared/f.txt", b"data", 1000.0)],
            [("shared/f.txt", b"data", 1000.0),
             ("unique-b/only.txt", b"unique", 1000.0)],
        )

    def test_unique_folder_not_safe_to_delete(self):
        r = self._result()
        safe_paths = [s["folder_path"] for s in r["safe_to_delete_roots"]]
        self.assertNotIn("unique-b", safe_paths)

    def test_shared_folder_is_safe_to_delete(self):
        r = self._result()
        safe_paths = [s["folder_path"] for s in r["safe_to_delete_roots"]]
        self.assertIn("shared", safe_paths)


# ─────────────────────────────────────────────────────────────────────
# Scenario 12: Mixed — some shared, some partially shared, some unique
# ─────────────────────────────────────────────────────────────────────

class TestMixedSharedPartialUnique(_AnalyzeHelper, unittest.TestCase):
    """Complex scenario combining shared, partially shared, and unique folders.
    A: common/f.txt, partial/x.txt
    B: common/f.txt, partial/x.txt, partial/y.txt, unique-b/z.txt
    Verifies all three relationships coexist correctly."""

    def _result(self):
        return self._run(
            [("common/f.txt",  b"shared",  1000.0),
             ("partial/x.txt", b"x",       1000.0)],
            [("common/f.txt",  b"shared",  1000.0),
             ("partial/x.txt", b"x",       1000.0),
             ("partial/y.txt", b"y",       1000.0),
             ("unique-b/z.txt", b"z",      1000.0)],
        )

    def test_common_is_identical(self):
        r = self._result()
        fc_map = self._fc_by_path(r)
        self.assertEqual(fc_map["common"]["relationship"], "identical")

    def test_partial_is_subset_superset(self):
        r = self._result()
        fc_map = self._fc_by_path(r)
        self.assertIn(fc_map["partial"]["relationship"],
                      ("subset/superset", "overlap"))

    def test_unique_b_is_unique(self):
        r = self._result()
        fc_map = self._fc_by_path(r)
        self.assertIn("unique-b", fc_map)
        self.assertEqual(fc_map["unique-b"]["relationship"], "unique")

    def test_all_three_in_html(self):
        r = self._result()
        html = cda.render_html(r)
        self.assertIn("common", html)
        self.assertIn("partial", html)
        self.assertIn("unique-b", html)


if __name__ == "__main__":
    unittest.main()
