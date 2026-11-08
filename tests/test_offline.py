import json
from pathlib import Path
from unittest.mock import patch
import requests
import unittest

import test_converter as baseline
from test_converter import FakeSession, Response
import converter


class InterruptedSession(FakeSession):
    def __init__(self, phase):
        super().__init__()
        self.phase = phase
        self.gets = 0

    def request(self, method, url, **kwargs):
        if method == 'POST' and self.phase == 'submission':
            raise requests.ConnectionError('offline')
        if method == 'GET':
            self.gets += 1
            if self.phase == 'poll' and self.gets == 2:
                raise requests.ConnectionError('offline')
        return super().request(method, url, **kwargs)

    def put(self, *args, **kwargs):
        if self.phase == 'upload':
            raise requests.Timeout('offline')
        return super().put(*args, **kwargs)

    def get(self, *args, **kwargs):
        if self.phase == 'download':
            raise requests.ConnectionError('offline')
        return super().get(*args, **kwargs)


class OfflineTests(baseline.ConverterTests):
    def test_auto_falls_back_for_all_network_stages(self):
        for phase in ('submission', 'upload', 'poll', 'download'):
            with self.subTest(phase=phase):
                output = self.root / phase
                engine = self.engine(output=output)
                engine.session = InterruptedSession(phase)
                with patch.object(engine, 'local', side_effect=self.fake_local) as local:
                    engine.run([self.pdf])
                local.assert_called_once()
                manifest = next(output.rglob('conversion.json'))
                self.assertEqual(json.loads(manifest.read_text())['backend'], 'local')
                jobs = list(output.rglob('cloud_job.json'))
                self.assertEqual(len(jobs), 0 if phase == 'submission' else 1)

    def test_cloud_only_does_not_fallback(self):
        engine = self.engine(mode='cloud')
        engine.session = InterruptedSession('submission')
        with patch.object(engine, 'local') as local:
            engine.run([self.pdf])
        local.assert_not_called()
        self.assertFalse(list(engine.output.rglob('conversion.json')))

    def test_cloud_job_can_be_recovered_after_local_fallback(self):
        engine = self.engine()
        engine.session = InterruptedSession('poll')
        with patch.object(engine, 'local', side_effect=self.fake_local):
            engine.run([self.pdf])
        resumed = self.engine(mode='cloud')
        resumed.session = FakeSession(states=['done'])
        resumed.run([self.pdf])
        self.assertEqual(resumed.session.posts, 0)
        self.assertEqual(len(list(resumed.output.rglob('conversion.json'))), 2)

    def test_broken_download_stream_falls_back(self):
        engine = self.engine()
        class BrokenResponse(Response):
            def iter_content(self, size):
                yield b'partial'
                raise requests.exceptions.ChunkedEncodingError('network interrupted')
        engine.session.get = lambda *args, **kwargs: BrokenResponse()
        with patch.object(engine, 'local', side_effect=self.fake_local) as local:
            engine.run([self.pdf])
        local.assert_called_once()
        self.assertTrue(list(engine.output.rglob('cloud_job.json')))
        self.assertEqual(json.loads(next(engine.output.rglob('conversion.json')).read_text())['backend'], 'local')

    def test_pause_during_interruption_prevents_starting_local(self):
        engine = self.engine()
        def interrupted(*args, **kwargs):
            engine.stop.set()
            raise requests.ConnectionError('offline')
        engine.session.put = interrupted
        with patch.object(engine, 'local') as local:
            engine.run([self.pdf])
        local.assert_not_called()
        self.assertTrue(list(engine.output.rglob('cloud_job.json')))

    def test_http_failures_do_not_trigger_connectivity_fallback(self):
        engine = self.engine()
        engine.session = FakeSession(rejection='A0202')
        with patch.object(engine, 'local') as local:
            engine.run([self.pdf])
        local.assert_not_called()

    def test_internet_probe_success_alternate_and_failure(self):
        probe_ok = Response()
        probe_ok.status_code, probe_ok.text = 200, 'Microsoft Connect Test'
        with patch.object(converter.requests, 'get', return_value=probe_ok) as get:
            self.assertTrue(converter.internet_reachable())
            self.assertEqual(get.call_count, 1)
            self.assertNotIn('headers', get.call_args.kwargs)
        with patch.object(converter.requests, 'get',
                          side_effect=[requests.ConnectionError(), Response()]) as get:
            self.assertTrue(converter.internet_reachable())
            self.assertEqual(get.call_count, 2)
        with patch.object(converter.requests, 'get', side_effect=requests.ConnectionError()):
            self.assertFalse(converter.internet_reachable())

    def test_captive_portal_response_does_not_mark_online(self):
        portal = Response()
        portal.status_code, portal.text = 200, 'Please sign in'
        with patch.object(converter.requests, 'get',
                          side_effect=[portal, requests.ConnectionError()]):
            self.assertFalse(converter.internet_reachable())


if __name__ == '__main__':
    unittest.main(verbosity=2)
