import os
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import patch

import test_converter
from converter import load_tokens, save_tokens


class PresentationTests(test_converter.ConverterTests):
    def test_token_storage_roundtrip_and_removal(self):
        store = self.root / 'private' / 'tokens.dpapi'
        secret = 'test-token-A;test-token-B'
        with patch('converter.token_store_path', return_value=store), \
                patch('converter.windows_protect', side_effect=lambda data, decrypt=False: data[::-1]):
            save_tokens(secret)
            self.assertNotIn(secret.encode(), store.read_bytes())
            self.assertEqual(load_tokens(), secret)
            save_tokens('')
            self.assertFalse(store.exists())

    def test_failed_encryption_keeps_previous_tokens(self):
        store = self.root / 'tokens.dpapi'
        store.write_bytes(b'old-encrypted')
        with patch('converter.token_store_path', return_value=store), \
                patch('converter.windows_protect', side_effect=OSError('encryption unavailable')):
            with self.assertRaises(OSError):
                save_tokens('new-token')
        self.assertEqual(store.read_bytes(), b'old-encrypted')

    def test_cloud_statuses_and_elapsed_events(self):
        engine = self.engine()
        engine.run([self.pdf])
        statuses = [event[2] for event in self.events if event[0] == 'status']
        self.assertIn('Uploading PDF to MinerU', statuses)
        self.assertIn('Processing at MinerU', statuses)
        self.assertIn('Downloading result from MinerU', statuses)
        self.assertIn('Checking and saving output', statuses)
        start = next(event[2] for event in self.events if event[0] == 'file_start')
        end = next(event[2] for event in self.events if event[0] == 'file_end')
        self.assertGreaterEqual(end, start)

    @unittest.skipIf(os.name == 'nt', 'Linux subprocess harness')
    def test_local_output_visible_before_process_finishes(self):
        executable = self.root / 'mineru-kit'
        executable.write_text('#!' + sys.executable + '\n'
            'import sys,zipfile,time\n'
            'print("LOCAL LIVE MARKER",flush=True)\n'
            'time.sleep(2)\n'
            'with zipfile.ZipFile(sys.argv[sys.argv.index("-o")+1],"w") as z:\n'
            ' z.writestr("markdown.md","# result")\n'
            ' z.writestr("middle_json.json","{}")\n')
        executable.chmod(0o755)
        received = threading.Event()
        engine = self.engine(mode='local', local_executable=str(executable))
        def emit(*event):
            self.events.append(event)
            if event[0] == 'log' and 'LOCAL LIVE MARKER' in event[1]:
                received.set()
        engine.emit = emit
        worker = threading.Thread(target=engine.run, args=([self.pdf],))
        worker.start()
        self.assertTrue(received.wait(3), 'Local output did not reach the UI event stream')
        self.assertTrue(worker.is_alive(), 'Output arrived only after the process finished')
        worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertTrue(list(engine.output.rglob('conversion.json')))


if __name__ == '__main__':
    unittest.main(verbosity=2)
