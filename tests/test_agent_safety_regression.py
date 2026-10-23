"""Synthetic filesystem regressions for the reconciled R9 mutation boundary."""
import asyncio
import json
from pathlib import Path
import tempfile
import unittest

from agent_mcp import capability_catalog, validate_arguments, ToolContractError
from agent_workspace import WorkspaceAccess
from agent_runtime import AgentRuntime
from agent_artifacts import ArtifactJournal


class MutationBoundaryTests(unittest.TestCase):
    def test_artifact_journal_rejects_mutations_before_reading_preview_or_db(self):
        journal=ArtifactJournal(None)
        for tool in ('edit_local_file','rename_local_file','delete_local_file'):
            with self.subTest(tool=tool), self.assertRaises(PermissionError):
                journal.prepare('task','chat',tool,{'path':'original.txt'},None)

    def test_low_level_windows_overwrite_is_blocked_before_os_access(self):
        from agent_workspace import WindowsHandles
        handles=WindowsHandles.__new__(WindowsHandles)
        with self.assertRaises(PermissionError):
            handles.child(None,'original.txt',overwrite=True)

    def test_unjournaled_mutations_are_not_available(self):
        from agent_mcp import WRITE_TOOLS
        for tool in ('edit_local_file', 'rename_local_file', 'delete_local_file'):
            with self.subTest(tool=tool):
                self.assertIn(tool, WRITE_TOOLS)

    def test_direct_mutations_preserve_original_and_destinations(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            access=WorkspaceAccess(root)
            try:
                for method in ('edit_file','rename_file','delete_file'):
                    original=root/(method+'.txt');original.write_text('original bytes')
                    arguments={'edit_file':(original.name,'changed'),
                               'rename_file':(original.name,'other.txt'),
                               'delete_file':(original.name,)}[method]
                    with self.subTest(operation=method):
                        with self.assertRaises(PermissionError):getattr(access,method)(*arguments)
                        self.assertEqual(original.read_text(),'original bytes')
                        self.assertFalse((root/'other.txt').exists())
            finally:access.close()

    def test_overwrite_open_is_rejected_before_truncation(self):
        with tempfile.TemporaryDirectory() as folder:
            original=Path(folder)/'original.txt';original.write_text('preserve this')
            access=WorkspaceAccess(folder)
            try:
                with self.assertRaises(PermissionError):
                    with access.descriptor('original.txt',overwrite=True):pass
                self.assertEqual(original.read_text(),'preserve this')
            finally:access.close()

    def test_absolute_path_is_not_silently_reinterpreted(self):
        with tempfile.TemporaryDirectory() as folder:
            access=WorkspaceAccess(folder)
            try:
                with self.assertRaises(PermissionError):access.clean_relative('/private.txt')
            finally:access.close()

    def test_success_for_one_file_cannot_clear_another_files_failure(self):
        class Client:
            def __init__(self,*a,**k):pass
            async def __aenter__(self):return self
            async def __aexit__(self,*a):pass
            async def call(self,tool,args,**kwargs):
                return {'error':'missing required file'} if args['path']=='missing.txt' else {'text':'available content'}
        with tempfile.TemporaryDirectory() as folder:
            plans=iter([{'tool':'read_local_file','arguments':{'path':'missing.txt'}},
                        {'tool':'read_local_file','arguments':{'path':'present.txt'}},
                        {'final':'Both files completed.'}])
            task=AgentRuntime(lambda *_:json.dumps(next(plans)),folder,None,'synthetic',lambda _:None,client_factory=Client)
            asyncio.run(task.run('Read missing.txt and present.txt.'))
            self.assertEqual(task.state,'PARTIAL')
            self.assertIn('missing required file',task.final)


if __name__=='__main__':unittest.main()
