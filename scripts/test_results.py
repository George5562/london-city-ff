import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import results


class ResultTests(unittest.TestCase):
    def test_confirmed_only_preserves_first_timestamp_and_corrects_scores(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'docs/data').mkdir(parents=True)
            with patch.object(results, 'ROOT', root):
                results.save_results([[1, 1, 2, 10, 9, 'UNDECIDED']], 'first')
                self.assertEqual(json.loads((root / 'docs/data/results.json').read_text()), [])
                results.save_results([[1, 1, 2, 10, 9, 'HOME']], 'confirmed')
                results.save_results([[1, 1, 2, 11, 9, 'HOME']], 'later')
                rows = json.loads((root / 'docs/data/results.json').read_text())
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]['at'], 'confirmed')
                self.assertEqual(rows[0]['homePts'], 11)
                self.assertNotIn('member', rows[0])


if __name__ == '__main__':
    unittest.main()
