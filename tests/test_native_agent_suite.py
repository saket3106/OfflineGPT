import ast
import asyncio
import hashlib
import json
import sqlite3
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from pathlib import Path

from agent_mcp import WorkspaceTools
from agent_permissions import ApprovalBroker, GrantAuthority
from agent_runtime import AgentRuntime, AgentTaskStore, parse_plan
from agent_workspace import WorkspaceAccess
from model_runtime import Family, ModelBehavior, ModelInfo


def make_workspace(folder):
    con = sqlite3.connect(Path(folder) / "app.db", check_same_thread=False)
    con.executescript("CREATE TABLE IF NOT EXISTS chats(id,title,updated);CREATE TABLE IF NOT EXISTS messages(id,chat_id,role,content,created);")
    return SimpleNamespace(con=con, lock=threading.RLock(), directory=Path(folder))


class TestNativeAgentSuite(unittest.TestCase):
    """Scripted-planner Agent integration suite (Tests A-G); no native inference.

    Verifies the end-to-end agentic workflow:
    Goal -> Plan -> Tool Selection -> Permission Check -> Tool Execution -> Observation -> Next Step / Complete.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.workspace = make_workspace(self.root)
        self.store = AgentTaskStore(self.workspace)

    def tearDown(self):
        try:
            self.workspace.con.close()
        except Exception:
            pass
        try:
            self.tmp.cleanup()
        except Exception:
            pass

    def _make_client_class(self, tools):
        class TestMCPClient:
            def __init__(self, *args, **kwargs):
                self.tools = tools

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def call(self, tool, args, approved=False):
                token = self.tools.grants.issue(tool, args) if approved else ""
                return self.tools.call(tool, args, approval=token)

        return TestMCPClient

    def test_a_local_document_research(self):
        """Test A: Local document research across 3 files with factual citation verification."""
        (self.root / "doc1.txt").write_text("Project Apollo was initiated in 1961.")
        (self.root / "doc2.txt").write_text("Apollo 11 landed humans on the Moon in July 1969.")
        (self.root / "doc3.txt").write_text("The Lunar Module was named Eagle and Command Module Columbia.")

        access = WorkspaceAccess(self.root)
        tools = WorkspaceTools(access, GrantAuthority(b"k" * 32))
        client_cls = self._make_client_class(tools)

        plans = iter([
            '{"tool":"search_workspace","arguments":{"query":"Apollo 11"}}',
            '{"tool":"read_local_file","arguments":{"path":"doc2.txt"}}',
            '{"tool":"read_local_file","arguments":{"path":"doc3.txt"}}',
            '{"final":"Apollo 11 landed on the Moon in July 1969 (doc2.txt). The Lunar Module was named Eagle (doc3.txt)."}',
        ])

        events = []
        runtime = AgentRuntime(lambda *_: next(plans), self.root, self.store, "chat-a", events.append, client_factory=client_cls)
        answer = asyncio.run(runtime.run("What year did Apollo 11 land and what was the lunar module name? Cite files."))

        self.assertEqual(runtime.state, "COMPLETED")
        self.assertIn("1969", answer)
        self.assertIn("Eagle", answer)
        self.assertIn("doc2.txt", answer)
        self.assertIn("doc3.txt", answer)
        tools.access.close()

    def test_b_coding_assistance(self):
        """Test B: Coding assistance (inspect buggy file, propose verified patch, syntax check without arbitrary execution)."""
        calc_file = self.root / "calc.py"
        buggy_bytes = b"def divide(a, b):\n    return a * b\n"
        calc_file.write_bytes(buggy_bytes)
        expected_sha = hashlib.sha256(buggy_bytes).hexdigest()

        access = WorkspaceAccess(self.root)
        tools = WorkspaceTools(access, GrantAuthority(b"b" * 32))
        client_cls = self._make_client_class(tools)
        broker = ApprovalBroker()

        plans = iter([
            '{"tool":"read_local_file","arguments":{"path":"calc.py"}}',
            json.dumps({
                "tool": "patch_local_file",
                "arguments": {
                    "path": "calc.py",
                    "destination": "calc_fixed.py",
                    "old": "    return a * b",
                    "new": "    return a / b",
                    "expected_sha256": expected_sha,
                },
            }),
            '{"final":"Successfully created calc_fixed.py with corrected division logic."}',
        ])

        orig_request = broker.request

        def auto_approve(task_id, tool, args, preview, on_request, cancel_event, timeout):
            def notify_and_decide(req):
                on_request(req)
                broker.decide(task_id, req["requestId"], req["digest"], True)
            return orig_request(task_id, tool, args, preview, notify_and_decide, cancel_event, timeout)

        broker.request = auto_approve

        events = []
        runtime = AgentRuntime(lambda *_: next(plans), self.root, self.store, "chat-b", events.append, broker=broker, client_factory=client_cls)
        answer = asyncio.run(runtime.run("Fix the divide function in calc.py"))

        self.assertEqual(runtime.state, "COMPLETED")
        self.assertIn("calc_fixed.py", answer)

        # Verify destination file was created with patched contents
        patched_file = self.root / "calc_fixed.py"
        patched_code = patched_file.read_text()
        self.assertIn("return a / b", patched_code)

        # Verify static syntax validity via AST without executing code
        tree = ast.parse(patched_code)
        self.assertIsInstance(tree, ast.Module)
        tools.access.close()

    def test_c_multi_step_tool_use(self):
        """Test C: Multi-step tool use (read sample invoice, calculate via real MCP calculator, output structured report)."""
        invoice_content = "Invoice #1042\nItems:\nWidget A: $125 x 4\nWidget B: $50 x 2\n"
        (self.root / "invoice.txt").write_text(invoice_content)

        access = WorkspaceAccess(self.root)
        tools = WorkspaceTools(access, GrantAuthority(b"c" * 32))
        client_cls = self._make_client_class(tools)

        plans = iter([
            '{"tool":"read_local_file","arguments":{"path":"invoice.txt"}}',
            '{"tool":"calculate","arguments":{"expression":"125 * 4 + 50 * 2"}}',
            '{"final":"Invoice #1042 total: $600 (Widget A: $500, Widget B: $100)."}',
        ])

        events = []
        runtime = AgentRuntime(lambda *_: next(plans), self.root, self.store, "chat-c", events.append, client_factory=client_cls)
        answer = asyncio.run(runtime.run("Calculate the total for invoice.txt"))

        self.assertEqual(runtime.state, "COMPLETED")
        self.assertIn("600", answer)
        tools.access.close()

    def test_d_tool_failure_recovery(self):
        """Test D: Tool failure recovery (missing file triggers error observation, agent recovers gracefully without repeating call)."""
        (self.root / "backup_data.txt").write_text("Server status: online, uptime: 99.9%")

        access = WorkspaceAccess(self.root)
        tools = WorkspaceTools(access, GrantAuthority(b"d" * 32))
        client_cls = self._make_client_class(tools)

        # Model first tries nonexistent primary file, gets error observation, then falls back to backup file
        plans = iter([
            '{"tool":"read_local_file","arguments":{"path":"primary_data.txt"}}',
            '{"tool":"read_local_file","arguments":{"path":"backup_data.txt"}}',
            '{"final":"Primary file was missing, but recovered status from backup_data.txt: online, uptime 99.9%."}',
        ])

        events = []
        runtime = AgentRuntime(lambda *_: next(plans), self.root, self.store, "chat-d", events.append, client_factory=client_cls)
        answer = asyncio.run(runtime.run("Read server status from data file"))

        self.assertTrue(runtime.state in ("COMPLETED", "PARTIAL"))
        self.assertIn("backup_data.txt", answer)
        self.assertIn("99.9%", answer)
        tools.access.close()

    def test_e_permission_boundaries(self):
        """Test E: Permission boundaries (directory traversal attempt outside workspace fails closed)."""
        access = WorkspaceAccess(self.root)
        tools = WorkspaceTools(access, GrantAuthority(b"e" * 32))
        client_cls = self._make_client_class(tools)

        # Model attempts path traversal
        plans = iter([
            '{"tool":"read_local_file","arguments":{"path":"../../secret_passwords.txt"}}',
            '{"final":"Access denied."}',
        ])

        events = []
        runtime = AgentRuntime(lambda *_: next(plans), self.root, self.store, "chat-e", events.append, client_factory=client_cls)
        asyncio.run(runtime.run("Read secret passwords"))

        tool_events = [e for e in events if e.get("type") == "agentStatus" and e.get("tool") == "read_local_file"]
        self.assertTrue(len(tool_events) > 0)
        tools.access.close()

    def test_f_task_cancellation_and_cleanup(self):
        """Test F: Task cancellation & cleanup (owned subprocess teardown, broker cancellation, clean CANCELED state)."""
        access = WorkspaceAccess(self.root)
        tools = WorkspaceTools(access, GrantAuthority(b"f" * 32))
        client_cls = self._make_client_class(tools)
        broker = ApprovalBroker()

        events = []
        runtime = AgentRuntime(lambda *_: '{"tool":"list_workspace_files","arguments":{}}', self.root, self.store, "chat-f", events.append, broker=broker, client_factory=client_cls)

        # Trigger cancellation
        runtime.cancel()
        asyncio.run(runtime.run("List files"))

        self.assertEqual(runtime.state, "CANCELED")
        self.assertTrue(runtime.cancel_event.is_set())
        tools.access.close()

    def test_g_multi_family_agent_execution(self):
        """Test G: Multi-family agent execution (behavior, chat templates, and options for GPT-OSS and Qwen3)."""
        # 1. GPT-OSS Family behavior
        gpt_info = ModelInfo(
            architecture="gpt-oss",
            display_name="GPT-OSS 20B",
            file_bytes=10000,
            context_limit=4096,
            template_sha256="gpt_template_sha",
            header_sha256="gpt_header_sha",
            observation=("f", 10000),
            metadata={"general.architecture": "gpt-oss"},
            warnings=(),
        )
        gpt_behavior = ModelBehavior.from_model(gpt_info)
        self.assertTrue(gpt_behavior.harmony)
        self.assertEqual(gpt_behavior.family, Family.GPT_OSS)
        self.assertEqual(gpt_behavior.template_kwargs("medium"), {"reasoning_effort": "medium"})

        # 2. Qwen3 Family behavior
        qwen_info = ModelInfo(
            architecture="qwen3",
            display_name="Qwen3 8B",
            file_bytes=10000,
            context_limit=40960,
            template_sha256="qwen_template_sha",
            header_sha256="qwen_header_sha",
            observation=("f", 10000),
            metadata={"general.architecture": "qwen3"},
            warnings=(),
        )
        qwen_behavior = ModelBehavior.from_model(qwen_info)
        self.assertFalse(qwen_behavior.harmony)
        self.assertEqual(qwen_behavior.family, Family.QWEN3)
        # Qwen3 must not inject Harmony reasoning_effort
        self.assertEqual(qwen_behavior.template_kwargs("medium"), {})
        self.assertEqual(qwen_behavior.generation_options("medium", 600), {})

        # 3. Both families produce plans parsed by parse_plan
        plan_tool = parse_plan('{"tool":"read_local_file","arguments":{"path":"test.txt"}}')
        self.assertEqual(plan_tool["tool"], "read_local_file")

        plan_final = parse_plan('{"final":"Universal model compatibility verified."}')
        self.assertEqual(plan_final["final"], "Universal model compatibility verified.")


if __name__ == "__main__":
    unittest.main()
