"""Fixed retrieval rules and adapter arguments; no public-source calls."""
import contextlib
import importlib.util
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from research_mcp import cli

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("evidence_eval", ROOT / "scripts/evaluate_evidence.py")
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)


class EvidenceEvaluationTest(unittest.TestCase):
    def test_all_twelve_fixed_offline_cases_pass_without_network(self):
        with patch("socket.socket.connect", side_effect=AssertionError("Unexpected network")):
            report = evaluation.evaluate()
        self.assertEqual(report["summary"], {"passed": 12, "checked": 12, "skipped": 0})
        self.assertEqual(len({case["id"] for case in report["cases"]}), 12)
        for case in report["cases"]:
            self.assertNotIn("content", case)
            self.assertNotIn("summary", case)

    def test_rule_hit_does_not_accept_html_with_matching_words(self):
        case = next(case for case in evaluation.CASES if case["id"] == "read-pores")
        hits = evaluation.assess(case, {"status": "ok", "data": {"content": "<html>salicylic acid irritates</html>"}})
        self.assertFalse(hits["readable_not_raw_html"])

    def test_upstream_failure_does_not_count_as_target_rejection(self):
        case = next(case for case in evaluation.CASES if case["id"] == "reject-private-target")
        hits = evaluation.assess(case, {"status": "error", "ok": False, "errors": [{"code": "UPSTREAM_ERROR"}]})
        self.assertFalse(hits["expected_error_code"])

    def test_authoritative_source_hit_requires_hostname_and_path(self):
        case = next(case for case in evaluation.CASES if case["id"] == "search-pores")
        target = 'aad.org/public/everyday-care/skin-care-secrets/face/treat-large-pores'
        for url, expected in (
            ('https://' + target, True),
            ('https://www.' + target + '/?campaign=test', True),
            ('https://evil.invalid/?target=' + target, False),
            ('https://aad.org.evil.invalid/' + target, False),
            ('https://aad.org@evil.invalid/public/', False),
            ('https://' + target + '-different-page', False),
            ('ftp://' + target, False),
        ):
            with self.subTest(url=url):
                hits = evaluation.assess(case, {'status': 'ok', 'candidates': [{'url': url}]})
                self.assertEqual(hits['expected_source_hit'], expected)

    def test_cli_age_is_optional_and_validated_by_the_service(self):
        for args, expected in (([], {}), (["--max-age-days", "90"], {"max_age_days": 90})):
            with self.subTest(args=args), patch.object(sys, "argv", ["research-mcp", "search", "classic", *args]), patch.object(cli.service, "search", return_value={"ok": True}) as search, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(), 0)
                search.assert_called_once_with("classic", limit=10, providers=None, **expected)
