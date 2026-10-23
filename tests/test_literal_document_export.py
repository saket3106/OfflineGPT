import tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from document_tools import literal_document_request,create_document_from_markdown,extract_document,OutputMode

class LiteralDocumentExportTests(unittest.TestCase):
    def test_explicit_payload_is_data_not_an_authoring_prompt(self):
        p='Create a PDF titled Sample Receipt with exactly the following text: Do not expand this. Quantity: 23.'
        self.assertEqual(literal_document_request(p,'.pdf'),{'title':'Sample Receipt','body':'Do not expand this. Quantity: 23.'})

    def test_normal_ambiguous_and_chat_requests_are_not_literal_exports(self):
        for prompt in ('Explain this PDF with exactly this sentence in its body: Hello.', 'Create a PDF about receipts.', 'Create a PDF with exactly this text: Receipt.'):
            self.assertIsNone(literal_document_request(prompt,'.pdf'))
        self.assertIsNone(literal_document_request('Create a presentation titled X with exactly this text: Y.','.pptx'))

    def test_actual_short_pdf_and_word_preserve_all_and_only_supplied_text(self):
        with tempfile.TemporaryDirectory() as folder:
            for extension in ('.pdf','.docx'):
                path,_=create_document_from_markdown('',extension,folder,literal_content={'title':'Receipt','body':'Paid 23.'})
                text,_=extract_document(path)
                self.assertEqual(' '.join(text.split()),'Receipt Paid 23.')

    def test_literal_request_does_not_override_chat_gate(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):create_document_from_markdown('','.pdf',folder,OutputMode.CHAT,literal_content={'title':'X','body':'Y'})
            self.assertFalse(list(Path(folder).iterdir()))

    def test_saved_literal_mismatch_is_rejected_and_new_file_removed(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch('document_tools.extract_document',return_value=('Unexpected extra paragraph',{})):
                with self.assertRaisesRegex(ValueError,'exact requested'):create_document_from_markdown('','.pdf',folder,literal_content={'title':'X','body':'Y'})
            self.assertFalse(list(Path(folder).iterdir()))

    def test_worker_skips_generation_and_stt_logs_follow_profile(self):
        import app
        class Engine:
            def ensure_chat(self,*args):raise AssertionError('Literal output must not load an LLM')
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);messages=[]
            with patch.object(app,'DATA_DIR',root),patch.object(app,'APP_DIR',root/'sealed'):
                worker=app.CreateDocumentWorker('', 'Create a PDF document titled New Note with exactly this sentence in its body: Keep these words unchanged.', '.pdf',Engine())
                worker.done.connect(messages.append);worker.run()
                self.assertEqual(len(messages),1)
                import json
                saved=json.loads(messages[0]);self.assertEqual(saved['content'],'New Note\n\nKeep these words unchanged.')
                class Profile:
                    voice_profiles={'turn':{'stages':{}}}
                app.App._save_voice_profile(Profile(),'turn')
                self.assertTrue((root/'logs/stt-profiles/turn.json').is_file())
                self.assertFalse((root/'sealed').exists())
