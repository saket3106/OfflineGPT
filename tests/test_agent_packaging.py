import ast
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from agent_packaging import (AgentPackagingError, collect_agent_inputs,
                             read_agent_lock, verify_agent_environment)

SOURCE = Path(__file__).resolve().parents[1]
LOCK = SOURCE / 'requirements-agent-windows-py312.lock'


def installed(pins):
    return [SimpleNamespace(metadata={'Name': name}, version=version)
            for name, version, _ in pins.values()]


class AgentPackagingTests(unittest.TestCase):
    def test_real_lock_pins_both_sdk_distributions(self):
        pins = read_agent_lock(LOCK)
        self.assertEqual(pins['mcp'][1], pins['mcp-types'][1])
        self.assertEqual(len(pins), 29)
        verify_agent_environment(pins, installed(pins))

    def test_missing_sdk_fails_before_collecting(self):
        def unexpected(*args, **kwargs):
            self.fail('Collector executed before dependency rejection')
        with self.assertRaisesRegex(AgentPackagingError, 'mcp:'):
            collect_agent_inputs(LOCK, distributions=[], collectors=(unexpected,) * 3)

    def test_wrong_transitive_version_fails(self):
        pins = read_agent_lock(LOCK)
        rows = installed(pins)
        next(row for row in rows if row.metadata['Name'] == 'pycparser').version = '3.0'
        with self.assertRaisesRegex(AgentPackagingError, 'pycparser'):
            verify_agent_environment(pins, rows)

    def test_duplicate_distributions_fail_even_if_versions_match(self):
        pins = read_agent_lock(LOCK)
        rows = installed(pins)
        rows.append(SimpleNamespace(metadata={'Name': 'MCP_TYPES'}, version='2.3.0'))
        with self.assertRaisesRegex(AgentPackagingError, 'mcp-types'):
            verify_agent_environment(pins, rows)

    def test_collects_dynamic_modules_metadata_and_data(self):
        calls = []
        def modules(name, **options):
            self.assertEqual(name, 'mcp')
            self.assertEqual(options['on_error'], 'raise')
            self.assertFalse(options['filter']('mcp.cli.main'))
            self.assertTrue(options['filter']('mcp.client.stdio'))
            return ['mcp.client.stdio', 'mcp.server', 'mcp.client.stdio']
        def metadata(name):
            calls.append(name)
            return [(name + '.dist-info', name + '.dist-info')]
        pins = read_agent_lock(LOCK)
        data, binary, hidden = collect_agent_inputs(LOCK, distributions=installed(pins),
            collectors=(modules, lambda _: [('mcp/py.typed', 'mcp')], metadata))
        self.assertEqual(set(calls), {name for name, _, _ in pins.values()})
        self.assertEqual(len(data), 30)
        self.assertEqual(binary, [])
        self.assertIn('anyio._backends._asyncio', hidden)
        self.assertIn('win32job', hidden)
        self.assertIn('pywintypes', hidden)
        self.assertEqual(len(hidden), len(set(hidden)))

    def test_missing_metadata_collection_fails_closed(self):
        def missing(_):
            raise FileNotFoundError('Required metadata missing')
        pins = read_agent_lock(LOCK)
        with self.assertRaises(FileNotFoundError):
            collect_agent_inputs(LOCK, distributions=installed(pins),
                collectors=(lambda *a, **kw: ['mcp'], lambda _: [], missing))

    def test_malformed_unhashed_duplicate_or_wrong_sdk_lock_rejected(self):
        valid = 'mcp==2.3.0 --hash=sha256:' + 'a' * 64 + '\n'
        types = 'mcp-types==2.3.0 --hash=sha256:' + 'b' * 64 + '\n'
        for content in [valid, valid + types + valid, valid + types.replace('2.3.0', '2.2.0'),
                        valid + types + 'unreviewed>=1.0\n', valid + types.replace('b' * 64, 'x' * 64)]:
            with self.subTest(content=content), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'lock'
                path.write_text(content, encoding='utf-8')
                with self.assertRaises(AgentPackagingError):
                    read_agent_lock(path)

    def test_spec_consumes_all_agent_inputs_before_analysis(self):
        tree = ast.parse((SOURCE / 'Offline GPT.spec').read_text(encoding='utf-8-sig'))
        analysis = next(node for node in ast.walk(tree) if isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name) and node.func.id == 'Analysis')
        arguments = {item.arg: ast.unparse(item.value) for item in analysis.keywords}
        for key, name in [('datas', 'agent_data'), ('binaries', 'agent_binaries'),
                          ('hiddenimports', 'agent_imports')]:
            self.assertIn(name, arguments[key])
        call = next(node for node in ast.walk(tree) if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name) and node.func.id == 'collect_agent_inputs')
        self.assertLess(call.lineno, analysis.lineno)


if __name__ == '__main__':
    unittest.main()
