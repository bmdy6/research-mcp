"""Keep both user-facing requirement tables complete as providers change."""
from pathlib import Path
import re
import unittest

from research_mcp.channel_catalog import CATALOG


class ChannelDocsTests(unittest.TestCase):
    def test_browser_requirement_tables_match_catalog_without_omissions(self):
        expected = [
            {r['id'] for r in CATALOG if r['cost_class'] != 'local_browser'},
            {r['id'] for r in CATALOG if r['cost_class'] == 'local_browser' and not r['requires_login']},
            {r['id'] for r in CATALOG if r['cost_class'] == 'local_browser' and r['requires_login']},
        ]
        for filename in ('README.md', 'README.en.md'):
            with self.subTest(filename=filename):
                text = (Path(__file__).resolve().parents[1] / filename).read_text()
                rows = [line for line in text.splitlines() if line.startswith('| **') and re.search(r'\b(?:35|10|14)\b', line)]
                self.assertEqual(len(rows), 3)
                self.assertEqual([set(re.findall(r'`([^`]+)`', row)) for row in rows], expected)
