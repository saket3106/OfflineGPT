"""Opt-in stack capture uses GIL-held frames and owned shutdown; no AI loads."""
import ast
import io
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import thread_diagnostics as diagnostics

class ThreadDiagnosticTests(unittest.TestCase):
    def test_sampler_captures_bounded_frames_and_cancels_without_late_write(self):
        stream=io.StringIO()
        with diagnostics.StackSampler(stream,.005,True) as sampler:
            deadline=time.monotonic()+2
            while not sampler.samples and time.monotonic()<deadline:time.sleep(.01)
            self.assertGreater(sampler.samples,0)
        self.assertFalse(sampler.thread.is_alive());self.assertIsNone(sampler.error);self.assertIn('test_sampler_captures_bounded_frames',stream.getvalue());stream.close()
    def test_cancellation_before_first_sample_preserves_original_exception(self):
        stream=io.StringIO()
        with self.assertRaisesRegex(ValueError,'original'):
            with diagnostics.StackSampler(stream,30) as sampler:raise ValueError('original')
        self.assertEqual(sampler.samples,0);self.assertFalse(sampler.thread.is_alive());stream.close()
    def test_owned_session_closes_sampler_and_stream_idempotently(self):
        with tempfile.TemporaryDirectory() as root:
            with patch('faulthandler.is_enabled',return_value=False),patch('faulthandler.enable') as enable,patch('faulthandler.disable') as disable:
                session=diagnostics.ThreadDiagnostics(Path(root)/'logs/threads.log',30).start()
                self.assertEqual(enable.call_count,1);session.close();session.close();self.assertEqual(disable.call_count,1)
                self.assertTrue(session.stream.closed);self.assertFalse(session.sampler.thread.is_alive())
    def test_preexisting_fatal_handler_is_preserved(self):
        with tempfile.TemporaryDirectory() as root:
            with patch('faulthandler.is_enabled',return_value=True),patch('faulthandler.enable') as enable,patch('faulthandler.disable') as disable:
                session=diagnostics.ThreadDiagnostics(Path(root)/'threads.log',30).start();session.close();enable.assert_not_called();disable.assert_not_called()
    def test_thread_start_failure_releases_file_and_fatal_handler(self):
        with tempfile.TemporaryDirectory() as root:
            session=diagnostics.ThreadDiagnostics(Path(root)/'threads.log',30)
            with patch('faulthandler.is_enabled',return_value=False),patch('faulthandler.enable'),patch('faulthandler.disable') as disable,patch.object(threading.Thread,'start',side_effect=RuntimeError('synthetic start failure')):
                with self.assertRaisesRegex(RuntimeError,'synthetic start'):session.start()
                self.assertTrue(session.closed);self.assertTrue(session.stream.closed);disable.assert_called_once()
    def test_application_opt_in_never_uses_native_timed_watchdog(self):
        text=(Path(__file__).resolve().parents[1]/'app.py').read_text(encoding='utf-8');tree=ast.parse(text)
        self.assertFalse(any(isinstance(node,ast.Attribute) and node.attr=='dump_traceback_later' for node in ast.walk(tree)))
        self.assertIn('ThreadDiagnostics',text);self.assertIn('_thread_diagnostics_session.close',text)
        self.assertIn("OFFLINE_GPT_THREAD_DIAGNOSTICS",text)

if __name__=='__main__':unittest.main()
