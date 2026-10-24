import contextlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bundle_drill.cli import main
from bundle_drill.core import DrillError, _header, isolated_environment, rehearse

DEMO = Path(__file__).resolve().parents[1] / "examples" / "demo.py"
spec = importlib.util.spec_from_file_location("bundle_demo", DEMO)
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)


class DrillTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base, self.update, self.expected = demo.create_chain(self.root)
        self.scratch = self.root / "scratch"
        self.scratch.mkdir()

    def drill(self, paths=None, **kwargs):
        return rehearse(paths or [self.base, self.update], scratch_parent=self.scratch, **kwargs)

    def test_genuine_chain_and_source_unchanged(self):
        before = {p: p.read_bytes() for p in (self.base, self.update)}
        result = self.drill(expected_refs=self.expected, exact=True)
        self.assertTrue(result["ok"])
        self.assertEqual(result["refs"], self.expected)
        self.assertEqual(result["fsck"], "passed")
        self.assertTrue(all(b["verified"] for b in result["bundles"]))
        self.assertEqual({p: p.read_bytes() for p in before}, before)
        self.assertEqual(list(self.scratch.iterdir()), [])

    def test_missing_base(self):
        report = self.drill([self.update])
        self.assertFalse(report["ok"])
        self.assertEqual(report["stage"], 0)
        self.assertEqual(list(self.scratch.iterdir()), [])

    def test_out_of_order(self):
        self.assertFalse(self.drill([self.update, self.base])["ok"])

    def test_full_only(self):
        self.assertTrue(self.drill([self.base])["ok"])

    def test_annotated_tag_preserves_tag_object(self):
        result = self.drill()
        self.assertEqual(result["refs"]["refs/tags/v1"], self.expected["refs/tags/v1"])

    def test_expected_mismatch(self):
        report = self.drill(expected_refs={"refs/heads/main": "0" * 40})
        self.assertFalse(report["ok"])
        self.assertEqual(report["mismatched_refs"], ["refs/heads/main"])

    def test_missing_expected_ref(self):
        self.assertFalse(self.drill(expected_refs={"refs/heads/absent": "1" * 40})["ok"])

    def test_exact_contract(self):
        report = self.drill(
            expected_refs={"refs/heads/main": self.expected["refs/heads/main"]}, exact=True
        )
        self.assertFalse(report["ok"])
        self.assertEqual(report["unexpected_refs"], ["refs/tags/v1"])

    def test_nonexact_contract(self):
        self.assertTrue(
            self.drill(expected_refs={"refs/heads/main": self.expected["refs/heads/main"]})["ok"]
        )

    def test_truncated_pack(self):
        self.update.write_bytes(self.update.read_bytes()[:-20])
        self.assertFalse(self.drill()["ok"])
        self.assertEqual(list(self.scratch.iterdir()), [])

    def test_size_limit_and_cleanup(self):
        with self.assertRaises(DrillError):
            self.drill(max_bytes=10)
        self.assertEqual(list(self.scratch.iterdir()), [])

    def test_time_limit(self):
        with self.assertRaises(DrillError):
            self.drill(timeout=0.00000001)
        self.assertEqual(list(self.scratch.iterdir()), [])

    def test_invalid_options(self):
        for options in (
            {"exact": True},
            {"expected_refs": []},
            {"expected_refs": {}},
            {"expected_refs": {"main": "1" * 40}},
            {"timeout": -1},
            {"max_bytes": 0},
        ):
            with self.subTest(options=options), self.assertRaises(DrillError):
                self.drill(**options)

    def test_invalid_header(self):
        self.base.write_bytes(b"not a git bundle")
        with self.assertRaises(DrillError):
            self.drill()
        self.assertEqual(list(self.scratch.iterdir()), [])

    def test_hostile_header_ref_cannot_inject_update(self):
        self.base.write_bytes(b"# v2 git bundle\n" + b"1" * 40 + b" refs/heads/main\x00oops\n\n")
        with self.assertRaises(DrillError):
            self.drill()

    def test_duplicate_and_oversized_header(self):
        ref = b"1" * 40 + b" refs/heads/main\n"
        for header in (
            b"# v2 git bundle\n" + ref + ref + b"\n",
            b"# v2 git bundle\n" + b"a" * 8193,
        ):
            self.base.write_bytes(header)
            with self.assertRaises(DrillError):
                self.drill()

    def test_unknown_filtered_capability(self):
        self.base.write_bytes(b"# v3 git bundle\n@filter=blob:none\n\n")
        with self.assertRaises(DrillError):
            self.drill()

    def test_missing_input(self):
        with self.assertRaises(DrillError):
            self.drill([self.root / "missing"])

    def test_empty_input(self):
        with self.assertRaises(DrillError):
            rehearse([])

    def test_no_git(self):
        with (
            patch("bundle_drill.core.shutil.which", return_value=None),
            self.assertRaises(DrillError),
        ):
            self.drill()

    def test_git_environment_isolated(self):
        injected = {
            "GIT_DIR": str(self.root),
            "GIT_OBJECT_DIRECTORY": str(self.root),
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "core.hooksPath",
            "GIT_CONFIG_VALUE_0": str(self.root),
            "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(self.root),
        }
        with patch.dict(os.environ, injected):
            env = isolated_environment()
            self.assertNotIn("GIT_DIR", env)
            self.assertNotIn("GIT_CONFIG_COUNT", env)
            self.assertNotIn("GIT_ALTERNATE_OBJECT_DIRECTORIES", env)
            self.assertTrue(self.drill()["ok"])

    def test_cli_contract_and_error_codes(self):
        contract = self.root / "expected.json"
        contract.write_text(json.dumps(self.expected))
        calls = [
            ([str(self.base), str(self.update), "--expect", str(contract), "--exact"], 0),
            ([str(self.update)], 1),
            ([str(self.base), "--max-bytes", "0"], 2),
        ]
        for args, expected in calls:
            with self.subTest(args=args), contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(main([*args, "--json"]), expected)
            self.assertIn("ok", json.loads(out.getvalue()))

    def test_duplicate_contract_keys(self):
        contract = self.root / "expected.json"
        contract.write_text('{"refs/heads/main":"x","refs/heads/main":"y"}')
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(main([str(self.base), "--expect", str(contract)]), 2)
        self.assertIn("duplicate", out.getvalue())

    def test_sha256_bundles(self):
        other = self.root / "sha256"
        other.mkdir()
        base, update, expected = demo.create_chain(other, object_format="sha256")
        result = self.drill([base, update], expected_refs=expected, exact=True)
        self.assertTrue(result["ok"])
        self.assertEqual(result["object_format"], "sha256")

    def test_mixed_formats(self):
        other = self.root / "sha256"
        other.mkdir()
        base, _, _ = demo.create_chain(other, object_format="sha256")
        with self.assertRaises(DrillError):
            self.drill([self.base, base])

    def test_header_info(self):
        fmt, refs = _header(self.base)
        self.assertEqual(fmt, "sha1")
        self.assertIn("refs/heads/main", refs)

    def test_nonfinite_timeout_refused(self):
        for timeout in (float("nan"), float("inf")):
            with self.subTest(timeout=timeout), self.assertRaises(DrillError):
                self.drill(timeout=timeout)

    def test_head_only_bundle_refused_explicitly(self):
        head_only = self.root / "head.bundle"
        demo.git_at(self.root / "source", "bundle", "create", str(head_only), "HEAD")
        with self.assertRaisesRegex(DrillError, "HEAD-only"):
            self.drill([head_only])


if __name__ == "__main__":
    unittest.main()
