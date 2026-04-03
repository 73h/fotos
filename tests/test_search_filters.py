import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.app.index.store import parse_search_filters  # noqa: E402


class SearchFilterParserTests(unittest.TestCase):
    def test_parse_person_unknown_sets_flag(self) -> None:
        terms, filters = parse_search_filters("urlaub person:unknown")
        self.assertEqual(terms, ["urlaub"])
        self.assertTrue(filters["person_unknown"])
        self.assertEqual(filters["persons"], [])

    def test_parse_person_unknown_is_case_insensitive(self) -> None:
        terms, filters = parse_search_filters("person:UnKnOwN")
        self.assertEqual(terms, [])
        self.assertTrue(filters["person_unknown"])
        self.assertEqual(filters["persons"], [])

    def test_parse_person_unknown_combines_with_named_person(self) -> None:
        terms, filters = parse_search_filters('person:"Marie Curie" person:unknown')
        self.assertEqual(terms, [])
        self.assertTrue(filters["person_unknown"])
        self.assertEqual(filters["persons"], ["Marie Curie"])


if __name__ == "__main__":
    unittest.main()

