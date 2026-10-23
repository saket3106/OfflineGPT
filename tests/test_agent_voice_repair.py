"""Regression checks for selected-model routing, image identity and loopback STT."""
import ssl
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import app
from agent_resources import AgentSpecialists, AttachmentRegistry
from agent_workspace import WorkspaceAccess
from integrated_runtime import EngineManager


class RepairTests(unittest.TestCase):
    def test_unknown_selected_model_never_falls_back_to_gpt_oss(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            default = root / "gpt-oss.gguf"
            default.write_bytes(b"default")
            manager = EngineManager()
            manager.cfg = {"gpt_oss_gguf": str(default), "chat_backend": "bundled"}
            manager.log_dir = root / "logs"
            self.assertEqual(manager.resolve_model_path(None), default)
            self.assertEqual(manager.resolve_model_path("openai/gpt-oss-20b"), default)
            qwen = root / "qwen.gguf"
            qwen.write_bytes(b"other")
            manager.runtime = type("Loaded", (), {"model_path": str(qwen)})()
            self.assertEqual(manager.resolve_model_path("default"), default)
            with self.assertRaisesRegex(RuntimeError, "selected local model is unavailable"):
                manager.resolve_model_path("missing-qwen-reference")
            with self.assertRaisesRegex(RuntimeError, "selected local model is unavailable"):
                manager.resolve_model_path(str(root / "missing-qwen.gguf"))

    def test_explicit_missing_image_never_substitutes_another_extension(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            Image.new("RGB", (8, 8), "red").save(root / "car.png")
            access = WorkspaceAccess(root)
            try:
                service = AgentSpecialists(None, access, AttachmentRegistry())
                with self.assertRaisesRegex(FileNotFoundError, "Use an exact path"):
                    service.read_image("workspace:car.jpg")
                self.assertEqual(service.read_image("workspace:car.png")[1]["name"], "car.png")
            finally:
                access.close()

    def test_whisper_loopback_http_never_initializes_tls(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ready")

            def log_message(self, *_args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.object(ssl, "create_default_context", side_effect=FileNotFoundError("missing CA")):
                opener = app.make_whisper_loopback_opener()
                with opener.open(f"http://127.0.0.1:{server.server_port}/", timeout=2) as response:
                    self.assertEqual(response.read(), b"ready")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


class LiveVoiceLifecycleTests(unittest.TestCase):
    def test_interrupt_keeps_worker_ownership_until_actual_completion(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        backend=SimpleNamespace(busy=True,cancel_current=Mock(),set_busy=Mock())
        bridge=SimpleNamespace(backend=backend,speech=Mock(),event=Mock(),_cancel_pending_speech=Mock())
        result=app.DesktopBridge.interruptVoice(bridge)
        self.assertTrue(__import__('json').loads(result)['ok'])
        backend.cancel_current.assert_called_once_with()
        backend.set_busy.assert_not_called()
        self.assertTrue(backend.busy)
        bridge._cancel_pending_speech.assert_called_once_with()

    def test_exit_cancels_pending_handoff_before_it_can_start_audio(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        import json
        cancel=threading.Event();workspace=Mock();speech=Mock()
        backend=SimpleNamespace(voice_mode_active=True,cancel_voice_input=Mock(),store=SimpleNamespace(workspace=workspace))
        bridge=SimpleNamespace(backend=backend,speech=speech,event=Mock(),speech_pending={'id':'pending','cancel':cancel})
        bridge._cancel_pending_speech=lambda:app.DesktopBridge._cancel_pending_speech(bridge)
        result=app.DesktopBridge.setLiveVoiceMode(bridge,False)
        self.assertTrue(json.loads(result)['ok'])
        self.assertTrue(cancel.is_set())
        self.assertIsNone(bridge.speech_pending)
        app.DesktopBridge._speech_prepared(bridge,'pending','')
        speech.speak.assert_not_called()
        speech.stop.assert_called_once_with()
        backend.cancel_voice_input.assert_called_once_with()
        workspace.finish.assert_called_once_with('pending','cancelled')

    def test_real_qt_interrupt_waits_for_worker_shutdown(self):
        import os,time
        os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
        from PySide6.QtCore import QObject,QThread,Signal
        from PySide6.QtWidgets import QApplication
        from types import SimpleNamespace
        from unittest.mock import Mock
        class Worker(QObject):
            cancelled=Signal()
            def __init__(self):
                super().__init__();self.requested=threading.Event();self.release=threading.Event()
            def cancel(self):self.requested.set()
            def run(self):
                self.requested.wait(3);self.release.wait(3);self.cancelled.emit()
        qt=QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as folder,patch.object(app,'DB_PATH',Path(folder)/'history.sqlite3'):
            backend=app.App();backend.new_chat()
            worker=Worker();thread=QThread();worker.moveToThread(thread)
            shutdown=threading.Event();thread.finished.connect(shutdown.set)
            backend.operation_event_id='voice-cancel-test';backend.set_busy(True)
            thread.started.connect(worker.run)
            backend.register_worker('image',thread,worker,[(worker.cancelled,backend.worker_cancelled)])
            bridge=SimpleNamespace(backend=backend,speech=Mock(),event=Mock(),_cancel_pending_speech=Mock())
            try:
                thread.start();app.DesktopBridge.interruptVoice(bridge)
                self.assertTrue(worker.requested.is_set())
                self.assertTrue(backend.busy)
                self.assertTrue(backend.stopping)
                self.assertIs(backend.active_worker,worker)
            finally:
                worker.release.set()
                deadline=time.monotonic()+5
                while (backend.busy or not shutdown.is_set()) and time.monotonic()<deadline:qt.processEvents();time.sleep(.01)
                qt.processEvents()
                self.assertFalse(backend.busy)
                self.assertIsNone(backend.active_worker)
                self.assertTrue(shutdown.is_set())
                backend.close();backend.store.con.close()


if __name__ == "__main__":
    unittest.main()
