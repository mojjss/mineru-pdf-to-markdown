import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

import requests
from pypdf import PdfWriter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from converter import Converter, ApiError, Paused, file_hash, output_lock, unpack_result, write_json


def result_bytes():
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as z:
        z.writestr('full.md', '# Test paper\n\nEquation $x_i=1$.\n')
        z.writestr('middle.json', '{"pages": [{"page_idx": 0}]}')
        z.writestr('images/plot.png', b'fake-image')
    return output.getvalue()


class Response:
    def __init__(self, payload=None, status=200):
        self.payload, self.status_code = payload, status

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError('stub HTTP error')

    def iter_content(self, size):
        yield result_bytes()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class FakeSession:
    def __init__(self, states=None, rejection=None):
        self.states = list(states or ['waiting-file', 'running', 'done'])
        self.rejection = rejection
        self.posts = self.uploads = self.downloads = 0
        self.digest = None

    def request(self, method, url, **kwargs):
        if method == 'POST':
            self.posts += 1
            if self.rejection:
                return Response({'code': self.rejection, 'msg': 'rejected'})
            self.digest = kwargs['json']['files'][0]['data_id']
            return Response({'code': 0, 'data': {
                'batch_id': 'batch1', 'file_urls': ['https://storage.invalid/upload']}})
        state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        if state == 'network-error':
            raise requests.ConnectionError('stub')
        return Response({'code': 0, 'data': {'extract_result': [{
            'state': state, 'data_id': self.digest,
            'full_zip_url': 'https://storage.invalid/result.zip'}]}})

    def put(self, url, data, **kwargs):
        assert 'headers' not in kwargs
        assert data.read(5) == b'%PDF-'
        self.uploads += 1
        return Response()

    def get(self, url, **kwargs):
        assert 'headers' not in kwargs
        self.downloads += 1
        return Response()

    def close(self):
        pass


class TokenSession(FakeSession):
    def __init__(self, limited=(), **kwargs):
        super().__init__(**kwargs)
        self.limited = set(limited)
        self.api_calls = []

    def request(self, method, url, **kwargs):
        token = kwargs['headers']['Authorization'].removeprefix('Bearer ')
        self.api_calls.append((method, token))
        if method == 'POST' and token in self.limited:
            return Response({'code': -60018, 'msg': 'daily limit'})
        return super().request(method, url, **kwargs)


class ConverterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.pdf = self.root / 'example.pdf'
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        writer.add_blank_page(width=100, height=100)
        writer.write(self.pdf)
        self.events = []

    def tearDown(self):
        self.temp.cleanup()

    def engine(self, **kwargs):
        settings = dict(output=self.root / 'out', token='private-test-token',
                        mode='auto', tier='standard', local_executable='missing',
                        page_cap=2000, emit=lambda *x: self.events.append(x),
                        poll_seconds=0, wait_seconds=30)
        settings.update(kwargs)
        engine = Converter(**settings)
        engine.session = FakeSession()
        return engine

    def fake_local(self, source, destination):
        destination.write_bytes(result_bytes())

    def test_cloud_normal_upload_extract_and_skip(self):
        engine = self.engine()
        engine.run([self.pdf])
        self.assertEqual(engine.session.posts, 1)
        self.assertEqual(engine.session.uploads, 1)
        manifest = next(engine.output.rglob('conversion.json'))
        data = json.loads(manifest.read_text())
        self.assertEqual(data['backend'], 'cloud')
        self.assertEqual(data['pages'], 2)
        self.assertTrue(engine.complete(manifest.parent))
        engine.run([self.pdf])
        self.assertEqual(engine.session.posts, 1)
        self.assertNotIn('private-test-token', ''.join(p.read_text() for p in engine.output.rglob('*.json')))

    def test_daily_limit_switches_remaining_files_to_local(self):
        second = self.root / 'second.pdf'
        second.write_bytes(self.pdf.read_bytes())
        engine = self.engine()
        engine.session = FakeSession(rejection=-60018)
        with patch.object(engine, 'local', side_effect=self.fake_local) as local:
            engine.run([self.pdf, second])
        self.assertEqual(local.call_count, 2)
        self.assertEqual(engine.session.posts, 1)
        self.assertEqual(len(list(engine.output.rglob('conversion.json'))), 2)

    def test_page_budget_uses_local_without_api(self):
        engine = self.engine(page_cap=1)
        with patch.object(engine, 'local', side_effect=self.fake_local) as local:
            engine.run([self.pdf])
        self.assertEqual(local.call_count, 1)
        self.assertEqual(engine.session.posts, 0)

    def test_page_budget_persists_across_runs(self):
        engine = self.engine(page_cap=2)
        engine.run([self.pdf])
        second = self.root / 'second.pdf'
        second.write_bytes(self.pdf.read_bytes())
        resumed = self.engine(page_cap=2)
        with patch.object(resumed, 'local', side_effect=self.fake_local) as local:
            resumed.run([second])
        self.assertEqual(local.call_count, 1)
        self.assertEqual(resumed.session.posts, 0)

    def test_authentication_failure_does_not_fallback(self):
        engine = self.engine()
        engine.session = FakeSession(rejection='A0202')
        with patch.object(engine, 'local') as local:
            engine.run([self.pdf])
        local.assert_not_called()
        self.assertFalse(list(engine.output.rglob('conversion.json')))

    def test_resume_existing_cloud_job_without_new_submission(self):
        engine = self.engine()
        engine.session = FakeSession(states=['waiting-file', 'network-error'])
        engine.run([self.pdf])
        self.assertEqual(engine.session.uploads, 1)
        self.assertFalse(list(engine.output.rglob('conversion.json')))
        resumed = self.engine()
        resumed.session = FakeSession(states=['done'])
        resumed.run([self.pdf])
        self.assertEqual(resumed.session.posts, 0)
        self.assertEqual(resumed.session.uploads, 0)
        self.assertTrue(list(engine.output.rglob('conversion.json')))

    def test_modes_keep_cloud_and_local_results(self):
        cloud = self.engine(mode='cloud')
        cloud.run([self.pdf])
        local = self.engine(mode='local')
        with patch.object(local, 'local', side_effect=self.fake_local):
            local.run([self.pdf])
        self.assertEqual(len(list(local.output.rglob('conversion.json'))), 2)

    def test_missing_asset_invalidates_completion(self):
        engine = self.engine()
        engine.run([self.pdf])
        manifest = next(engine.output.rglob('conversion.json'))
        next(manifest.parent.rglob('plot.png')).unlink()
        self.assertFalse(engine.complete(manifest.parent))

    def test_pause_preserves_job_and_does_not_start_local(self):
        engine = self.engine()
        engine.stop.set()
        with patch.object(engine, 'local') as local:
            engine.run([self.pdf])
        self.assertEqual(engine.session.posts, 0)
        local.assert_not_called()

    def test_rate_limit_retries_and_does_not_fallback(self):
        engine = self.engine()
        engine.session.request = lambda *args, **kwargs: Response(status=429)
        with patch.object(engine, 'wait') as wait, patch.object(engine, 'local') as local:
            engine.run([self.pdf])
        self.assertEqual(wait.call_count, 3)
        local.assert_not_called()

    def test_pending_timeout_keeps_cloud_job(self):
        engine = self.engine(wait_seconds=-1)
        engine.session = FakeSession(states=['pending'])
        with patch.object(engine, 'local') as local:
            engine.run([self.pdf])
        self.assertEqual(len(list(engine.output.rglob('cloud_job.json'))), 1)
        local.assert_not_called()

    def test_corrupt_archive_cannot_be_published(self):
        archive = self.root / 'bad.zip'
        archive.write_bytes(b'not a zip')
        with self.assertRaises(zipfile.BadZipFile):
            unpack_result(archive, self.root / 'content')
        self.assertFalse((self.root / 'content').exists())

    def test_archive_traversal_rejected(self):
        archive = self.root / 'bad.zip'
        with zipfile.ZipFile(archive, 'w') as bundle:
            bundle.writestr('../escape.md', 'bad')
        with self.assertRaises(ValueError):
            unpack_result(archive, self.root / 'content')
        self.assertFalse((self.root / 'escape.md').exists())

    def test_empty_markdown_rejected(self):
        archive = self.root / 'bad.zip'
        with zipfile.ZipFile(archive, 'w') as bundle:
            bundle.writestr('full.md', '')
            bundle.writestr('middle.json', '{}')
        with self.assertRaises(ValueError):
            unpack_result(archive, self.root / 'content')

    def test_output_lock_rejects_second_writer(self):
        with output_lock(self.root):
            with self.assertRaises(RuntimeError):
                with output_lock(self.root):
                    pass

    def test_multiple_tokens_switch_on_daily_limit(self):
        engine = self.engine(token='first;second')
        engine.session = TokenSession(limited=['first'])
        engine.run([self.pdf])
        self.assertEqual([t for method, t in engine.session.api_calls if method == 'POST'],
                         ['first', 'second'])
        job = json.loads(next(engine.output.rglob('cloud_job.json')).read_text())
        import hashlib
        self.assertEqual(job['token_id'], hashlib.sha256(b'second').hexdigest()[:16])
        self.assertTrue(list(engine.output.rglob('conversion.json')))

    def test_all_tokens_limited_then_local(self):
        engine = self.engine(token='first;second')
        engine.session = TokenSession(limited=['first', 'second'])
        with patch.object(engine, 'local', side_effect=self.fake_local) as local:
            engine.run([self.pdf])
        self.assertEqual(local.call_count, 1)
        self.assertEqual(engine.session.api_calls, [('POST', 'first'), ('POST', 'second')])

    def test_per_token_page_budget_switch(self):
        engine = self.engine(token='first;second', page_cap=2)
        engine.session = TokenSession()
        engine.run([self.pdf])
        second_pdf = self.root / 'second.pdf'
        second_pdf.write_bytes(self.pdf.read_bytes())
        engine.run([second_pdf])
        self.assertEqual([t for method, t in engine.session.api_calls if method == 'POST'],
                         ['first', 'second'])

    def test_reordered_tokens_resume_with_original_owner(self):
        first = self.engine(token='first;second')
        first.session = TokenSession(states=['waiting-file', 'network-error'])
        first.run([self.pdf])
        resumed = self.engine(token='second;first')
        resumed.session = TokenSession(states=['done'])
        resumed.run([self.pdf])
        self.assertEqual(resumed.session.api_calls, [('GET', 'first')])
        self.assertTrue(list(first.output.rglob('conversion.json')))

    def test_old_single_token_jobs_resume(self):
        first = self.engine(token='first')
        first.session = TokenSession(states=['waiting-file', 'network-error'])
        first.run([self.pdf])
        job_path = next(first.output.rglob('cloud_job.json'))
        job = json.loads(job_path.read_text())
        del job['token_id']
        write_json(job_path, job)
        resumed = self.engine(token='second;first')
        resumed.session = TokenSession(states=['done'])
        resumed.run([self.pdf])
        self.assertEqual(resumed.session.api_calls, [('GET', 'first')])

    def test_missing_owner_does_not_resubmit_or_fallback(self):
        first = self.engine(token='first')
        first.session = TokenSession(states=['waiting-file', 'network-error'])
        first.run([self.pdf])
        resumed = self.engine(token='second')
        resumed.session = TokenSession(states=['done'])
        with patch.object(resumed, 'local') as local:
            resumed.run([self.pdf])
        self.assertEqual(resumed.session.api_calls, [])
        local.assert_not_called()

    def test_tokens_deduplicated(self):
        engine = self.engine(token=' first; second\nfirst, third ')
        self.assertEqual(engine.tokens, ['first', 'second', 'third'])

    def test_confirmed_failed_quota_job_rotates(self):
        engine = self.engine(token='first;second')
        session = TokenSession()
        request = session.request
        def response(method, url, **kwargs):
            result = request(method, url, **kwargs)
            if method == 'GET' and kwargs['headers']['Authorization'] == 'Bearer first':
                return Response({'code': 0, 'data': {'extract_result': [{
                    'state': 'failed', 'err_code': -60018, 'data_id': session.digest}]}})
            return result
        session.request = response
        engine.session = session
        engine.run([self.pdf])
        self.assertTrue(list(engine.output.rglob('conversion.json')))
        self.assertEqual(len(list(engine.output.rglob('cloud_job_limited_*.json'))), 1)
        self.assertEqual([t for method, t in session.api_calls if method == 'POST'], ['first', 'second'])

    @unittest.skipIf(os.name == 'nt', 'Linux subprocess harness')
    def test_local_subprocess_entry_point(self):
        executable = self.root / 'mineru-kit'
        executable.write_text('#!' + sys.executable + '\n'
            'import sys,zipfile,os\n'
            'assert os.environ["MINERU_MODEL_SOURCE"] == "local"\n'
            'assert os.environ["MINERU_MODEL_SMALL_BACKEND"] == "onnx"\n'
            'with zipfile.ZipFile(sys.argv[sys.argv.index("-o")+1],"w") as z:\n'
            ' z.writestr("markdown.md","# subprocess result")\n'
            ' z.writestr("middle_json.json","{}")\n')
        executable.chmod(0o755)
        engine = self.engine(mode='local', local_executable=str(executable))
        engine.run([self.pdf])
        self.assertEqual(len(list(engine.output.rglob('conversion.json'))), 1)
        self.assertEqual(engine.session.posts, 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
