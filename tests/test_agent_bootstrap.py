import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import agent_mcp


class BootstrapTests(unittest.TestCase):
    def test_optional_sdk_absence_does_not_import_it(self):
        with patch.object(agent_mcp,'SDK_STARTUP_MS',None),patch.object(agent_mcp,'SDK_STARTUP_ERROR',''),patch.object(agent_mcp,'sdk_ready',return_value=False):
            agent_mcp.prime_agent_sdk()
            self.assertIsNone(agent_mcp.SDK_STARTUP_MS)

    def test_public_backend_initialization_happens_once(self):
        calls=[]
        def run(function,backend):calls.append(backend);asyncio.run(function())
        with patch.object(agent_mcp,'SDK_STARTUP_MS',None),patch.object(agent_mcp,'SDK_STARTUP_ERROR',''),patch.object(agent_mcp,'sdk_ready',return_value=True),patch.dict('sys.modules',{'mcp':SimpleNamespace(Client=object),'mcp.server':SimpleNamespace(MCPServer=object),'win32api':SimpleNamespace(),'win32job':SimpleNamespace(),'pywintypes':SimpleNamespace(),'anyio':SimpleNamespace(run=run)}):
            agent_mcp.prime_agent_sdk();agent_mcp.prime_agent_sdk()
            self.assertEqual(calls,['asyncio']);self.assertIsInstance(agent_mcp.SDK_STARTUP_MS,float)
            self.assertEqual(agent_mcp.SDK_STARTUP_ERROR,'')

    def test_bootstrap_failure_disables_agent_not_normal_application(self):
        def broken(*a,**k):raise RuntimeError('test dependency unavailable')
        with patch.object(agent_mcp,'SDK_STARTUP_MS',None),patch.object(agent_mcp,'SDK_STARTUP_ERROR',''),patch.object(agent_mcp.importlib.metadata,'version',return_value='2.3.0'),patch.dict('sys.modules',{'mcp':SimpleNamespace(Client=object),'mcp.server':SimpleNamespace(MCPServer=object),'win32api':SimpleNamespace(),'win32job':SimpleNamespace(),'pywintypes':SimpleNamespace(),'anyio':SimpleNamespace(run=broken)}):
            agent_mcp.prime_agent_sdk()
            self.assertFalse(agent_mcp.sdk_ready());self.assertIn('test dependency unavailable',agent_mcp.SDK_STARTUP_ERROR)

    def test_sdk_and_type_distribution_must_both_match(self):
        for types in ('2.2.0',None):
            def version(name):
                if name=='mcp':return '2.3.0'
                if types is None:raise agent_mcp.importlib.metadata.PackageNotFoundError(name)
                return types
            with patch.object(agent_mcp,'SDK_STARTUP_ERROR',''),patch.object(agent_mcp.importlib.metadata,'version',side_effect=version):
                self.assertFalse(agent_mcp.sdk_ready())

    def test_missing_native_budget_extension_disables_agent(self):
        with patch.object(agent_mcp.os,'name','nt'),patch.object(agent_mcp,'SDK_STARTUP_MS',None),patch.object(agent_mcp,'SDK_STARTUP_ERROR',''),patch.object(agent_mcp,'sdk_ready',return_value=True),patch.dict('sys.modules',{'mcp':SimpleNamespace(Client=object),'mcp.server':SimpleNamespace(MCPServer=object),'win32api':None}):
            agent_mcp.prime_agent_sdk()
            self.assertIn('win32api',agent_mcp.SDK_STARTUP_ERROR)

    def test_real_app_bootstrap_precedes_pyside_import_hook(self):
        tree=ast.parse((Path(__file__).resolve().parents[1]/'app.py').read_text(encoding='utf-8'))
        first_qt=min(n.lineno for n in ast.walk(tree) if isinstance(n,ast.ImportFrom) and (n.module or '').startswith('PySide6'))
        calls=[n.lineno for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='prime_agent_sdk']
        self.assertEqual(len(calls),1);self.assertLess(calls[0],first_qt)


if __name__=='__main__':unittest.main()
