"""Acceptance test exercising a disposable workspace, approval barrier, image inspection, and recoverable mutations."""
import hashlib
import tempfile
import unittest
from pathlib import Path
from PIL import Image

from agent_permissions import ApprovalBroker
from agent_resources import AgentSpecialists, AttachmentRegistry
from agent_workspace import WorkspaceAccess


class DisposableWorkspaceAcceptanceTests(unittest.TestCase):
    def test_disposable_workspace_read_write_approval_and_denial(self):
        """
        Validates that in a disposable workspace:
        1. Valid files can be read.
        2. Non-existent files cleanly raise FileNotFoundError (not raw NTSTATUS).
        3. Approved new files can be written.
        4. In-place edit/rename/delete without recoverable protocol are denied with PermissionError.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            
            # Setup a sample file
            sample_file = root / "sample.txt"
            sample_file.write_text("Hello from OfflineGPT disposable workspace!", encoding="utf-8")
            
            access = WorkspaceAccess(root)
            broker = ApprovalBroker()
            
            try:
                # 1. Read existing file
                content = access.read("sample.txt").decode("utf-8")
                self.assertEqual(content, "Hello from OfflineGPT disposable workspace!")
                
                # 2. Non-existent file raises FileNotFoundError
                with self.assertRaises(FileNotFoundError):
                    access.read("does_not_exist.txt")
                    
                # 3. Create new file via approved write
                receipt = access.write_new("output.txt", "Approved generated content")
                self.assertEqual(receipt['path'], "output.txt")
                self.assertFalse(receipt['originalOverwritten'])
                self.assertTrue((root / "output.txt").exists())
                
                # 4. In-place destructive operations without approval are strictly denied
                with self.assertRaisesRegex(PermissionError, "In-place editing is not supported"):
                    access.edit_file("sample.txt", "Overwritten content")
                    
                with self.assertRaisesRegex(PermissionError, "Rename requires a recoverable"):
                    access.rename_file("sample.txt", "renamed.txt")
                    
                with self.assertRaisesRegex(PermissionError, "Deletion requires a recoverable"):
                    access.delete_file("sample.txt")
                    
                # Verify sample.txt was not modified or deleted
                self.assertEqual((root / "sample.txt").read_text(encoding="utf-8"), "Hello from OfflineGPT disposable workspace!")
                
            finally:
                access.close()

    def test_real_image_pixels_inspection_in_workspace(self):
        """
        Validates that an actual image with real pixels in the workspace
        is loaded, validated, and processed without guessing or path spoofing.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            img_path = root / "badge.png"
            # Create a 64x64 blue image
            img = Image.new("RGB", (64, 64), color=(0, 102, 204))
            img.save(img_path)
            
            access = WorkspaceAccess(root)
            registry = AttachmentRegistry()
            specialists = AgentSpecialists(None, access, registry)
            
            try:
                # Read image through specialists
                data, info = specialists.read_image("workspace:badge.png")
                self.assertGreater(len(data), 0)
                self.assertEqual(info['name'], "badge.png")
                self.assertEqual(info['source'], "authorized workspace")
                
                # Verify that the returned bytes are actual valid image pixels
                import io
                with Image.open(io.BytesIO(data)) as decoded:
                    self.assertEqual(decoded.size, (64, 64))
                    self.assertEqual(decoded.format, "PNG")
                    # Inspect actual pixel color
                    pixel = decoded.getpixel((0, 0))
                    self.assertEqual(pixel[:3], (0, 102, 204))
                
                # Missing image with wrong extension fails closed
                with self.assertRaisesRegex(FileNotFoundError, "Use an exact path"):
                    specialists.read_image("workspace:badge.jpg")
            finally:
                access.close()

    def test_authorized_recoverable_mutation_lifecycle_and_rollback(self):
        """
        Validates the complete human-authorized, recoverable file mutation workflow:
        1. Pre-flight preview with expected SHA-256 and diff generation.
        2. Rejection of unauthorized mutations and concurrent conflict detection.
        3. Execution of approved in-place edit with transaction receipt and atomic backup.
        4. Atomic rollback restoring the exact original file content.
        5. Authorized rename and delete workflows with atomic rollback.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            original_text = "VERSION 1: Critical configuration data.\nLine 2: Active.\n"
            target_file = root / "config.txt"
            target_file.write_bytes(original_text.encode('utf-8'))
            orig_sha = hashlib.sha256(original_text.encode('utf-8')).hexdigest()

            access = WorkspaceAccess(root)
            try:
                # 1. Prepare edit mutation
                new_text = "VERSION 2: Updated configuration data.\nLine 2: Active.\n"
                preview = access.prepare_mutation('edit', 'config.txt', new_content=new_text)
                self.assertEqual(preview['action'], 'edit')
                self.assertEqual(preview['path'], 'config.txt')
                self.assertEqual(preview['expected_sha256'], orig_sha)
                self.assertIn('-VERSION 1', preview['diff'])
                self.assertIn('+VERSION 2', preview['diff'])

                # 2. Rejection on concurrent modification / hash mismatch
                tampered_ticket = dict(preview, expected_sha256='0' * 64)
                with self.assertRaisesRegex(ValueError, "Conflict detected"):
                    access.edit_file('config.txt', new_text, approval_ticket=tampered_ticket)

                # 3. Authorized execution of edit
                receipt = access.edit_file('config.txt', new_text, approval_ticket=preview)
                self.assertTrue(receipt['ok'])
                self.assertEqual(receipt['status'], 'COMMITTED')
                self.assertEqual(target_file.read_text(encoding='utf-8'), new_text)

                # 4. Rollback of edit
                rb_receipt = access.rollback_mutation(receipt['transaction_id'])
                self.assertTrue(rb_receipt['ok'])
                self.assertEqual(rb_receipt['status'], 'ROLLED_BACK')
                self.assertEqual(target_file.read_text(encoding='utf-8'), original_text)

                # 5. Authorized rename and rollback
                rename_preview = access.prepare_mutation('rename', 'config.txt', new_path='archived_config.txt')
                rename_receipt = access.rename_file('config.txt', 'archived_config.txt', approval_ticket=rename_preview)
                self.assertTrue(rename_receipt['ok'])
                self.assertFalse((root / 'config.txt').exists())
                self.assertTrue((root / 'archived_config.txt').exists())

                # Rollback rename
                access.rollback_mutation(rename_receipt['transaction_id'])
                self.assertTrue((root / 'config.txt').exists())
                self.assertFalse((root / 'archived_config.txt').exists())

                # 6. Authorized delete and rollback
                delete_preview = access.prepare_mutation('delete', 'config.txt')
                delete_receipt = access.delete_file('config.txt', approval_ticket=delete_preview)
                self.assertTrue(delete_receipt['ok'])
                self.assertFalse((root / 'config.txt').exists())

                # Rollback delete
                access.rollback_mutation(delete_receipt['transaction_id'])
                self.assertTrue((root / 'config.txt').exists())
                self.assertEqual((root / 'config.txt').read_text(encoding='utf-8'), original_text)

            finally:
                access.close()


if __name__ == '__main__':
    unittest.main()
