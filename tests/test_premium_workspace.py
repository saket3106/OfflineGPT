import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import app
from intelligence import analyze_request, build_system_prompt, is_standalone_greeting


class PremiumWorkspaceRegression(unittest.TestCase):
    def test_compact_greeting_is_context_selection_not_an_answer(self):
        for text in ['Hello', 'Hello ChatGPT, how are you?', 'Hi again! How is your day going?', 'Hello! Nice to see you again.']:
            self.assertTrue(is_standalone_greeting(text), text)
        for text in ['Hello, summarize the PDF', 'Hi, what is TCP?', 'Hello\n1. Calculate 7 * 8', 'Explain the greeting in the document']:
            self.assertFalse(is_standalone_greeting(text), text)
        analysis = analyze_request('Hello')
        analysis['compact_conversation'] = True
        system = build_system_prompt('Long policy ' * 1000, analysis, history_context='IRRELEVANT PDF ' * 1000)
        self.assertLess(len(system), 500)
        self.assertNotIn('IRRELEVANT', system)
        self.assertNotIn('Hello! How can', system)

    def test_document_followup_keeps_its_normal_prompt(self):
        analysis = analyze_request('What is the calibration interval in the PDF?', has_document=True)
        system = build_system_prompt('Use evidence', analysis, rag_context='Calibration interval: 17 days.')
        self.assertIn('17 days', system)

    def test_image_sampling_not_whole_job_completion(self):
        state = app.image_progress_event('Generating image · step 12 of 12')
        self.assertEqual(state['state'], 'SAMPLING')
        self.assertEqual(state['percent'], 99)
        self.assertEqual(state['totalSteps'], 12)
        self.assertIsNone(app.image_progress_event('Loading local image model…')['percent'])
        self.assertEqual(app.image_progress_event('Saving and verifying the image…')['state'], 'SAVING')
        self.assertEqual(app.image_progress_event('Decoding image…')['state'], 'DECODING')
        self.assertIsNone(app.image_progress_event('Generating image · 70%')['percent'])

    def test_tensor_and_vae_counters_are_not_sampling_steps(self):
        phase, text = app.image_cli_progress('|########| 12/12 - 1.03GB/s', 'flux', 'LOADING_MODEL', 12)
        self.assertEqual(phase, 'LOADING_MODEL'); self.assertIsNone(text)
        phase, text = app.image_cli_progress('sampling using Euler method', 'flux', phase, 12)
        self.assertIsNone(text)
        phase, text = app.image_cli_progress('|====> | 3/12 - 2.23s/it', 'flux', phase, 12)
        self.assertEqual(phase, 'SAMPLING'); self.assertIn('step 3 of 12', text)
        phase, text = app.image_cli_progress('decoding latent 1/1', 'flux', phase, 12)
        self.assertEqual(phase, 'DECODING')
        phase, text = app.image_cli_progress('|====>| 5/21 - 16.95it/s', 'flux', phase, 12)
        self.assertIsNone(text)

    def test_first_image_progress_does_not_require_a_previous_chat(self):
        from unittest.mock import Mock
        events = []
        backend = SimpleNamespace(active_kind='image', operation_bubble=Mock(), ready=Mock(), operation_event_id='image-first', emit_frontend=events.append, frontend_bridge=None)
        app.App.on_worker_progress(backend, 'Loading local image model…')
        self.assertEqual(events[-1]['message']['imageProgress']['state'], 'LOADING_MODEL')

    def test_document_exact_worker_emits_metrics_without_model(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'ledger.txt'
            source.write_text('Calibration interval: 17 days.', encoding='utf-8')
            worker = app.DocumentWorker('test', 'Search the document for the exact phrase "Calibration interval".', str(source), '', None)
            metrics, answers, errors = [], [], []
            worker.metrics.connect(metrics.append)
            worker.done.connect(answers.append)
            worker.failed.connect(errors.append)
            worker.run()
            self.assertEqual(errors, [])
            self.assertIn('FOUND', json.loads(answers[0])['content'])
            self.assertGreaterEqual(metrics[-1]['document_total_ms'], 0)

    def test_document_chat_forwards_child_metrics(self):
        class Engine:
            runtime = SimpleNamespace(last_generation_diagnostics={'prompt_token_count': 90, 'timings': {'prompt_ms': 120, 'predicted_ms': 250, 'predicted_n': 20}})
            def ensure_chat(self, *args): return 'test'
            def chat_completion(self, messages, **kwargs):
                answer = 'The calibration interval is 17 days.'
                kwargs['on_token'](answer)
                return answer
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'ledger.txt'; source.write_text('Calibration interval: 17 days.')
            worker = app.DocumentWorker('test', 'Explain the calibration interval in this document.', str(source), '', Engine())
            metrics, errors = [], []
            worker.metrics.connect(metrics.append); worker.failed.connect(errors.append)
            worker.run()
            self.assertEqual(errors, [])
            self.assertTrue(any(m.get('model_calls') == 1 for m in metrics))
            self.assertEqual(next(m for m in metrics if 'speed' in m)['speed'], 80)
            self.assertEqual(next(m for m in metrics if 'prefill_ms' in m)['prefill_ms'], 120)

    def test_metrics_belong_to_each_response_and_survive_storage(self):
        backend = SimpleNamespace(operation_event_id='a', _last_send_t1=time.perf_counter() - 2, _response_metrics={'a': {'prompt_tokens': 90}, 'b': {'prompt_tokens': 100}})
        first = app.App.completed_metrics(backend)
        backend.operation_event_id = 'b'; backend._last_send_t1 = time.perf_counter() - 3
        second = app.App.completed_metrics(backend)
        with tempfile.TemporaryDirectory() as folder, patch.object(app, 'DB_PATH', Path(folder) / 'history.sqlite3'):
            store = app.Store(); cid = store.new_chat()
            store.add(cid, 'assistant', 'First', metrics=first); store.add(cid, 'assistant', 'Second', metrics=second)
            store.con.close()
            store = app.Store(); rows = store.messages(cid)
            self.assertEqual(json.loads(rows[0][3])['request_id'], 'a')
            self.assertEqual(json.loads(rows[1][3])['request_id'], 'b')
            self.assertGreater(json.loads(rows[0][3])['duration_seconds'], 1.9)
            store.con.close()

    def test_browser_stream_measurement_persists_before_final_row(self):
        import threading
        with tempfile.TemporaryDirectory() as folder, patch.object(app, 'DB_PATH', Path(folder)/'history.sqlite3'), patch.object(app, 'APP_DIR', Path(folder)):
            store = app.Store(); cid = store.new_chat()
            backend = SimpleNamespace(busy=True, operation_event_id='stream-a', current=cid, store=store, _response_metrics={}, _last_send_t1=time.perf_counter())
            bridge = SimpleNamespace(backend=backend)
            app.DesktopBridge.recordRendering(bridge, json.dumps({'requestId':'stream-a','firstAnswerVisibleMs':123.4}))
            saved = app.App.completed_metrics(backend)
            self.assertEqual(saved['frontend']['firstAnswerVisibleMs'],123.4)
            store.add(cid,'assistant','Answer',metrics=saved)
            backend.busy=False
            app.DesktopBridge.recordRendering(bridge,json.dumps({'requestId':'stream-a','finalAnswerVisibleMs':567.8}))
            result=json.loads(store.messages(cid)[0][3])
            self.assertEqual(result['frontend']['firstAnswerVisibleMs'],123.4)
            self.assertEqual(result['frontend']['finalAnswerVisibleMs'],567.8)
            self.assertEqual(backend._response_metrics,{})
            store.con.close()

    def test_cancelled_thread_releases_controller_for_next_request(self):
        from unittest.mock import Mock
        thread = object()
        backend = SimpleNamespace(active_thread=thread, busy=True, stopping=True, set_busy=Mock())
        app.App._worker_thread_finished(backend, thread)
        backend.set_busy.assert_called_once_with(False, 'Stopped')
        backend.set_busy.reset_mock()
        app.App._worker_thread_finished(backend, object())
        backend.set_busy.assert_not_called()
        self.assertGreaterEqual(app.App.staticMetaObject.indexOfMethod('_worker_thread_finished(PyObject)'), 0)

    def test_real_qt_cancel_then_document_request(self):
        import os
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from PySide6.QtCore import QObject, QThread, Signal
        from PySide6.QtWidgets import QApplication
        from unittest.mock import Mock
        import threading
        class Worker(QObject):
            cancelled = Signal()
            def __init__(self):
                super().__init__(); self.cancel_event = threading.Event()
            def cancel(self): self.cancel_event.set()
            def run(self):
                self.cancel_event.wait(3)
                self.cancelled.emit()
        qt = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as folder, patch.object(app, 'DB_PATH', Path(folder)/'history.sqlite3'):
            backend = app.App(); backend.new_chat()
            worker = Worker(); thread = QThread(); worker.moveToThread(thread)
            backend.operation_event_id = 'cancel-test'; backend.set_busy(True)
            thread.started.connect(worker.run)
            backend.register_worker('image', thread, worker, [(worker.cancelled, backend.worker_cancelled)])
            thread.start(); backend.cancel_current()
            deadline = time.monotonic() + 5
            while backend.busy and time.monotonic() < deadline:
                qt.processEvents(); time.sleep(.01)
            self.assertFalse(backend.busy)
            source = Path(folder)/'source.txt'; source.write_text('Aurora Workshop')
            backend.send('Search the document for the exact phrase "Aurora Workshop".', [str(source)])
            deadline = time.monotonic() + 5
            while backend.busy and time.monotonic() < deadline:
                qt.processEvents(); time.sleep(.01)
            self.assertFalse(backend.busy)
            self.assertIn('FOUND', backend.store.messages(backend.current)[-1][1])
            qt.processEvents(); backend.close(); backend.store.con.close()

    def test_emergency_unload_preserved_even_in_keep_loaded_mode(self):
        from integrated_runtime import EngineManager
        from unittest.mock import Mock
        manager = EngineManager()
        manager.cfg['model_lifecycle_mode'] = 'fast'
        manager.cfg['emergency_ram_threshold_mb'] = 256
        manager.loaded_model = 'test'
        manager._unload_chat = Mock()
        with patch('integrated_runtime.physical_memory_status', return_value={'available': 64 * 1024**2, 'pagefile_available': 8 * 1024**3}):
            manager._active_inference_count = 1
            self.assertFalse(manager.check_emergency_memory())
            manager._unload_chat.assert_not_called()
            manager._active_inference_count = 0
            self.assertTrue(manager.check_emergency_memory())
            manager._unload_chat.assert_called_once()


if __name__ == '__main__': unittest.main()
