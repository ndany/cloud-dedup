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
    pass  # populated in Task 4


class TestAnalyzeIntegration(unittest.TestCase):
    pass  # populated in Task 3


if __name__ == "__main__":
    unittest.main()
