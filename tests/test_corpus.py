"""Offline corpus safety/provenance tests; no competition files required."""

import hashlib
import io
import json
import lzma
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sat_eval.corpus import (
    _download_cnf, _lineage, _TableParser, fetch, import_holdout,
    load_manifest, make_smoke, safe_path, verify,
)


class CorpusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def write_manifest(self, instances, split="development", name="manifest.json", **extra):
        path = self.root / name
        path.write_text(json.dumps({"schema_version": 1, "split": split,
                                    "instances": instances, **extra}))
        return path

    def item(self, content=b"p cnf 1 1\n1 0\n", **extra):
        return {"id": "one", "path": "one.cnf", "family": "one-family",
                "lineage": "one-generator", "sha256": hashlib.sha256(content).hexdigest(),
                "expected_result": "sat", "source": {"url": "https://benchmark-database.de/file/abc"}, **extra}

    def test_smoke_round_trip(self):
        make_smoke(self.root)
        result = verify(self.root / "manifest.json", self.root)
        self.assertEqual(result["instances"], 6)
        self.assertEqual(load_manifest(self.root / "manifest.json")["split"], "smoke")

    def test_changed_contents_rejected(self):
        make_smoke(self.root)
        (self.root / "empty.cnf").write_text("p cnf 1 0\n")
        with self.assertRaisesRegex(ValueError, "mismatch"):
            verify(self.root / "manifest.json", self.root)

    def test_unpinned_catalog_is_not_evaluable(self):
        item = self.item()
        del item["sha256"]
        manifest = self.write_manifest([item])
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            load_manifest(manifest)
        self.assertEqual(len(load_manifest(manifest, allow_unpinned=True)["instances"]), 1)

    def test_duplicate_ids_and_contents_rejected(self):
        item = self.item()
        for other in (dict(item), dict(item, id="two", path="two.cnf")):
            manifest = self.write_manifest([item, other])
            with self.assertRaisesRegex(ValueError, "duplicate"):
                load_manifest(manifest)

    def test_paths_cannot_escape(self):
        for name in ("../secret", "/etc/passwd", "a/../../secret", ""):
            with self.assertRaisesRegex(ValueError, "unsafe"):
                safe_path(self.root, name)
        (self.root / "escape").symlink_to(self.root.parent)
        with self.assertRaisesRegex(ValueError, "escapes"):
            safe_path(self.root, "escape/secret")

    def test_table_parser_preserves_links_text_and_entities(self):
        parser = _TableParser()
        parser.feed('<table><tr><th>hash</th><th>family</th></tr>'
                    '<tr><td><a href="/file/id">abc</a></td><td>x&amp;y</td></tr></table>')
        self.assertEqual(parser.rows, [["hash", "family"], ["abc", "x&y"]])

    def test_download_hashed_and_bounded(self):
        content = b"p cnf 1 1\n1 0\n"
        archive = lzma.compress(content)
        def response(*args, **kwargs):
            result = io.BytesIO(archive)
            result.headers = {"Content-Length": str(len(archive))}
            return result
        with patch("sat_eval.corpus._request", response):
            locked, downloaded, unpacked = _download_cnf(self.item(), self.root, 1024, 1024)
        self.assertEqual(downloaded, len(archive))
        self.assertEqual(unpacked, len(content))
        self.assertEqual(locked["source"]["archive_sha256"], hashlib.sha256(archive).hexdigest())
        self.assertEqual((self.root / "one.cnf").read_bytes(), content)

    def test_download_and_expansion_over_budget_leave_no_input(self):
        content = b"p cnf 1 1\n" + b"c" * 10000
        archive = lzma.compress(content)
        def response(*args, **kwargs):
            result = io.BytesIO(archive)
            result.headers = {"Content-Length": str(len(archive))}
            return result
        with patch("sat_eval.corpus._request", response):
            for network, unpacked in ((1, 10000), (10000, 10)):
                with self.assertRaisesRegex(ValueError, "budget"):
                    _download_cnf(self.item(content), self.root, network, unpacked)
                self.assertFalse((self.root / "one.cnf").exists())

    def test_fetch_refuses_holdouts(self):
        manifest = self.write_manifest([self.item()], split="final")
        with self.assertRaisesRegex(ValueError, "only acquires"):
            fetch(manifest, self.root, self.root / "locked.json")

    def test_refetch_of_locked_manifest_is_stable(self):
        content = b"p cnf 1 1\n1 0\n"
        item = self.item(content)
        (self.root / "one.cnf").write_bytes(content)
        manifest = self.write_manifest([item], kind="locked_corpus", acquisition_catalog_sha256="f" * 64)
        before = load_manifest(manifest)
        fetch(manifest, self.root, manifest)
        self.assertEqual(load_manifest(manifest), before)

    def test_holdout_must_be_external(self):
        manifest = self.write_manifest([self.item()], split="final")
        with self.assertRaisesRegex(ValueError, "outside"):
            import_holdout(manifest, self.root / "locked.json", self.root, manifest, repo_root=self.root)

    def test_holdout_rejects_family_lineage_and_exact_overlap(self):
        item = self.item()
        development = self.write_manifest([item], name="dev.json")
        (self.root / "one.cnf").write_bytes(b"p cnf 1 1\n1 0\n")
        for family, lineage in (("one-family", "other-generator"), ("other-family", "one-generator"), ("other-family", "other-generator")):
            holdout_item = dict(item, family=family, lineage=lineage,
                                source={"provenance": "Unit test private input"})
            holdout = self.write_manifest([holdout_item], split="final", curator_lineage_review="Reviewed.")
            with self.assertRaisesRegex(ValueError, "overlap|duplicate"):
                import_holdout(holdout, self.root / "locked.json", self.root, development, repo_root=self.root / "visible")

    def test_known_metadata_aliases_share_lineage(self):
        self.assertEqual(_lineage("minimal-disagreement-parity"), _lineage("minimum-disagreement-parity"))
        self.assertEqual(_lineage("hardware-bmc"), _lineage("hardware-model-checking"))

    def test_import_rejects_isomorphic_holdout_duplicates(self):
        development = self.write_manifest([self.item()], name="dev.json")
        content = b"p cnf 2 1\n1 2 0\n"
        other = b"p cnf 2 1\n2 1 0\n"
        items = [self.item(content, id="two", path="two.cnf", family="private", lineage="private",
                           source={"provenance": "Example", "isohash2": "abc123"}),
                 self.item(other, id="three", path="three.cnf", family="private", lineage="private",
                           source={"provenance": "Reordered example", "isohash2": "abc123"})]
        (self.root / "two.cnf").write_bytes(content)
        (self.root / "three.cnf").write_bytes(other)
        holdout = self.write_manifest(items, split="final", curator_lineage_review="Reviewed.")
        with self.assertRaisesRegex(ValueError, "isomorphic holdout"):
            import_holdout(holdout, self.root / "locked.json", self.root, development, repo_root=self.root / "visible")


if __name__ == "__main__":
    unittest.main()
