import contextlib
import io
import unittest
from unittest.mock import patch

from extraction_progress import NotebookProgress


class ProgressLifecycleTests(unittest.TestCase):
    def test_failure_is_visible_and_stops_background_updates(self):
        output = io.StringIO()
        with patch('IPython.get_ipython', return_value=None), contextlib.redirect_stdout(output):
            with self.assertRaisesRegex(ValueError, 'bad input'):
                with NotebookProgress() as indicator:
                    indicator(2, 'Parsing')
                    raise ValueError('bad input')
        self.assertFalse(indicator._thread.is_alive())
        self.assertIn('Failed', output.getvalue())
        self.assertIn('bad input', output.getvalue())
        self.assertNotIn('Complete', output.getvalue())

    def test_success_stops_background_updates(self):
        output = io.StringIO()
        with patch('IPython.get_ipython', return_value=None), contextlib.redirect_stdout(output):
            with NotebookProgress() as indicator:
                indicator(6, 'Saved files')
        self.assertFalse(indicator._thread.is_alive())
        self.assertIn('Complete', output.getvalue())
        self.assertIn('6/6', output.getvalue())
