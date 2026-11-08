from pathlib import Path
from unittest.mock import patch
import os
import unittest

import test_converter as baseline
from test_converter import TokenSession


class DestinationTests(baseline.ConverterTests):
    def test_multiple_inputs_use_their_chosen_output(self):
        second = self.root / 'folder_b' / 'second.pdf'
        second.parent.mkdir()
        second.write_bytes(self.pdf.read_bytes())
        third = self.root / 'folder_c' / 'third.pdf'
        third.parent.mkdir()
        third.write_bytes(self.pdf.read_bytes())
        custom_output = self.root / 'chosen folder'
        engine = self.engine(mode='local')
        original = engine.output
        with patch.object(engine, 'local', side_effect=self.fake_local):
            engine.run([self.pdf, second, third], {str(second): custom_output, str(third): custom_output})
        default_manifests = list(original.rglob('conversion.json'))
        custom_manifests = list(custom_output.rglob('conversion.json'))
        self.assertEqual(len(default_manifests), 1)
        self.assertEqual(len(custom_manifests), 2)
        self.assertEqual(engine.output, original)

    def test_relative_and_absolute_same_output_locked_once(self):
        second = self.root / 'second.pdf'
        second.write_bytes(self.pdf.read_bytes())
        relative_output = Path(os.path.relpath(self.root / 'out', Path.cwd()))
        engine = self.engine(mode='local', output=relative_output)
        with patch.object(engine, 'local', side_effect=self.fake_local):
            engine.run([self.pdf, second], {str(second): self.root / 'out'})
        self.assertEqual(len(list((self.root / 'out').rglob('conversion.json'))), 2)
        self.assertEqual(engine.output, relative_output)

    def test_page_budget_still_tracked_per_output_and_token(self):
        second = self.root / 'second.pdf'
        second.write_bytes(self.pdf.read_bytes())
        custom_output = self.root / 'out-b'
        engine = self.engine(token='first;second', page_cap=2)
        engine.session = TokenSession()
        engine.run([self.pdf, second], {str(second): custom_output})
        posts = [token for method, token in engine.session.api_calls if method == 'POST']
        self.assertEqual(posts, ['first', 'first'])
        self.assertEqual(len(list((self.root / 'out').rglob('conversion.json'))), 1)
        self.assertEqual(len(list(custom_output.rglob('conversion.json'))), 1)

    def test_input_generators_remain_supported(self):
        engine = self.engine(mode='local')
        with patch.object(engine, 'local', side_effect=self.fake_local):
            engine.run(iter([self.pdf]))
        self.assertEqual(len(list(engine.output.rglob('conversion.json'))), 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
