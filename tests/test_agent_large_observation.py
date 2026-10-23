"""Regression tests for agent observation head-and-tail bounding, tail-only evidence, and completion safety."""
import tempfile
import unittest
from pathlib import Path

from agent_permissions import canonical
from agent_runtime import bound_observation_payload
from agent_workspace import WorkspaceAccess
from agent_mcp import WorkspaceTools


class TestAgentObservationBounding(unittest.TestCase):
    def test_large_file_content_retains_head_and_tail_evidence(self):
        """
        When a tool returns a large file (e.g. 15,000 chars), bound_observation_payload
        must retain both head and tail evidence with explicit omission metadata,
        rather than only retaining the head or omitting the whole result.
        """
        head_signature = "def calculate_energy(mass, velocity):\n"
        middle_padding = "    # padding comment for line range testing\n" * 400
        tail_signature = "    return 0.5 * mass * velocity ** 2\n"
        large_text = head_signature + middle_padding + tail_signature

        raw_result = {
            'path': 'physics/energy.py',
            'text': large_text,
            'bytes': len(large_text.encode())
        }
        self.assertGreater(len(canonical(raw_result).encode()), 6000)

        bounded = bound_observation_payload(raw_result, max_bytes=5500)
        self.assertLessEqual(len(canonical(bounded).encode()), 5500)

        # Both head and tail evidence must be present
        self.assertIn('def calculate_energy', bounded.get('text', ''))
        self.assertIn('return 0.5 * mass * velocity ** 2', bounded.get('text', ''))

        # Omission and truncation metadata must be explicitly set
        self.assertTrue(bounded.get('omitted'))
        self.assertTrue(bounded.get('truncated'))
        self.assertTrue(bounded.get('truncated_content'))
        self.assertIn('resultSha256', bounded)

        # Coverage must report partial head-and-tail coverage
        coverage = bounded.get('coverage', {})
        self.assertEqual(coverage.get('mode'), 'head_and_tail')
        self.assertFalse(coverage.get('coverage_complete'))
        self.assertGreater(coverage.get('omitted_chars', 0), 0)
        self.assertEqual(coverage.get('total_chars'), len(large_text))

    def test_decisive_evidence_only_at_end_of_large_file_is_preserved(self):
        """
        Critical test: Essential evidence appears ONLY at the very end of a 30,000-character
        file. The observation bounding must retain this tail evidence while strictly
        preserving omitted=True so the runtime marks the task as PARTIAL.
        """
        boiler_plate = "Lorem ipsum dolor sit amet, consectetur adipiscing elit. Line %d\n"
        long_preamble = "".join(boiler_plate % i for i in range(500))
        secret_tail = "FINAL_TRANSACTION_STATUS = 'CRITICAL_AUDIT_VERIFIED_98765'\n"
        large_text = long_preamble + secret_tail

        raw_result = {'path': 'audit/ledger.py', 'content': large_text}
        bounded = bound_observation_payload(raw_result, max_bytes=5500)

        # Tail must contain the decisive audit token
        self.assertIn("CRITICAL_AUDIT_VERIFIED_98765", bounded.get('content', ''))
        # Head must contain the initial preamble
        self.assertIn("Lorem ipsum dolor sit amet", bounded.get('content', ''))

        # Must flag omission so agent cannot claim full uninspected file verification
        self.assertTrue(bounded.get('omitted'))
        self.assertIn('OMITTED SPAN', bounded.get('content', ''))

    def test_large_file_listing_retains_head_and_tail_items(self):
        """
        When a workspace has many files (e.g. 150), the listing retains first 10
        and last 10 files with an omission marker, instead of erasing the listing.
        """
        files = [{'path': f'src/controllers/module_item_{i:03d}.py', 'bytes': 1024} for i in range(150)]
        raw_result = {'files': files, 'truncated': False}
        self.assertGreater(len(canonical(raw_result).encode()), 6000)

        bounded = bound_observation_payload(raw_result, max_bytes=5500)
        self.assertLessEqual(len(canonical(bounded).encode()), 5500)
        self.assertIn('truncated_items', bounded)
        self.assertTrue(bounded.get('omitted'))

        retained_files = bounded['files']
        # 10 head + 1 omission marker + 10 tail = 21 entries
        self.assertEqual(len(retained_files), 21)
        self.assertEqual(retained_files[0]['path'], 'src/controllers/module_item_000.py')
        self.assertEqual(retained_files[-1]['path'], 'src/controllers/module_item_149.py')
        self.assertTrue(retained_files[10].get('omitted'))

    def test_scoped_line_range_retrieval_for_omitted_middle(self):
        """
        Validates that when the middle of a file is omitted from initial observation,
        the agent can retrieve specific targeted line ranges using read_local_file.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            test_file = root / "script.py"
            lines = [f"line_{i:03d} = {i}\n" for i in range(1, 201)]
            test_file.write_text("".join(lines), encoding="utf-8")

            from agent_permissions import GrantAuthority
            access = WorkspaceAccess(root)
            grants = GrantAuthority(b"\x00" * 32)
            try:
                tools = WorkspaceTools(access, grants)
                # Retrieve lines 50 to 59
                res = tools.call('read_local_file', {'path': 'script.py', 'start_line': '50', 'line_count': '10'})
                self.assertEqual(res['start_line'], 50)
                self.assertEqual(res['end_line'], 59)
                self.assertEqual(res['total_lines'], 200)
                self.assertIn('line_050 = 50', res['text'])
                self.assertIn('line_059 = 59', res['text'])
                self.assertNotIn('line_049 = 49', res['text'])
                self.assertNotIn('line_060 = 60', res['text'])
            finally:
                access.close()


if __name__ == '__main__':
    unittest.main()
