import hashlib
import os
from pathlib import Path
import struct
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import model_inspection as m


def string(value):
    raw=value.encode('utf-8');return struct.pack('<Q',len(raw))+raw


def scalar(kind,value):
    if kind==8:return string(value)
    if kind==9:
        subtype,items=value
        return struct.pack('<IQ',subtype,len(items))+b''.join(scalar(subtype,item) for item in items)
    return struct.pack('<'+m._Reader.FORMATS[kind],value)


def fixture(rows=None,*,version=3,kind=0,offset=0,tail=1024):
    if rows is None:rows=[('general.architecture',8,'fixture'),('tokenizer.ggml.model',8,'fixture'),
                         ('tokenizer.ggml.tokens',9,(8,['one','two'])),('tokenizer.chat_template',8,'{{ messages }}')]
    raw=b'GGUF'+struct.pack('<IQQ',version,1,len(rows))
    raw+=b''.join(string(key)+struct.pack('<I',tag)+scalar(tag,value) for key,tag,value in rows)
    raw+=string('fixture.weight')+struct.pack('<IQIQ',1,1,kind,offset)
    raw+=b'\0'*((-len(raw))%32)+b'\0'*tail
    return raw


class InspectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='offlinegpt-gguf-inspection-')
        self.addCleanup(self.tmp.cleanup);self.path=Path(self.tmp.name).resolve()/'fixture.gguf'

    def inspect(self,raw=None):
        self.path.write_bytes(fixture() if raw is None else raw)
        original=hashlib.sha256(self.path.read_bytes()).hexdigest()
        result=m.inspect_gguf(self.path)
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(),original)
        return result

    def test_v2_and_v3_metadata_never_grant_native_support(self):
        for version in (2,3):
            result=self.inspect(fixture(version=version))
            self.assertEqual(result['version'],version);self.assertEqual(result['warnings'],[])
            self.assertEqual(result['status'],'STATIC_METADATA_INSPECTED');self.assertFalse(result['nativeCompatible'])
            self.assertIsNone(result['fullModelSha256']);self.assertEqual(result['tensorDataIntegrity'],'NOT_VERIFIED')

    def test_unknown_tensor_type_is_reported_without_claiming_compatibility(self):
        result=self.inspect(fixture(kind=0xffffffff))
        self.assertEqual(result['tensorTypeIds'],[0xffffffff]);self.assertFalse(result['nativeCompatible'])

    def test_tensor_body_is_not_read(self):
        result=self.inspect(fixture(tail=1024**2))
        self.assertLess(result['bytesRead'],1024);self.assertEqual(result['fileSizeBytes'],self.path.stat().st_size)

    def test_untrusted_template_is_hashed_never_executed(self):
        template='{{ dangerous.run("do not execute") }}'
        result=self.inspect(fixture([('tokenizer.chat_template',8,template)]))
        value=result['metadata']['tokenizer.chat_template']
        self.assertEqual(value['sha256'],hashlib.sha256(template.encode()).hexdigest());self.assertFalse(value['trusted'])
        self.assertNotIn(template,str(result))

    def test_missing_architecture_tokenizer_and_template_are_explicit(self):
        result=self.inspect(fixture([]));self.assertEqual(len(result['warnings']),3)

    def test_nested_arrays_are_read_bounded_without_retaining_values(self):
        result=self.inspect(fixture([('tokenizer.ggml.tokens',9,(9,[(8,['one']),(8,['two'])]))]))
        self.assertEqual(result['metadata']['tokenizer.ggml.tokens'],{'arrayType':9,'count':2})
        self.assertTrue(any('Tokenizer' in warning for warning in result['warnings']))
        self.assertFalse(result['nativeCompatible'])

    def test_split_assets_are_not_assumed_complete(self):
        result=self.inspect(fixture([('split.count',4,3)]))
        self.assertTrue(any('Sharded' in warning for warning in result['warnings']))

    def test_truncated_header_metadata_and_tensor_info_rejected(self):
        for length in (0,4,8,24,40,80):
            self.path.write_bytes(fixture()[:length])
            with self.assertRaises(m.InspectionError):m.inspect_gguf(self.path)

    def test_wrong_magic_version_and_byte_order_rejected(self):
        for raw in (b'nope'+fixture()[4:],fixture(version=4),fixture(version=0x03000000)):
            self.path.write_bytes(raw)
            with self.assertRaises(m.InspectionError):m.inspect_gguf(self.path)

    def test_invalid_and_duplicate_keys_rejected(self):
        for rows in ([('bad/key',4,1)],[('General.X',4,1)],[('general.x',4,1),('general.x',4,2)]):
            self.path.write_bytes(fixture(rows))
            with self.assertRaises(m.InspectionError):m.inspect_gguf(self.path)

    def test_invalid_bool_float_template_and_alignment_rejected(self):
        for row in (('general.x',7,2),('general.x',6,float('nan')),('tokenizer.chat_template',4,1),('general.alignment',4,3)):
            self.path.write_bytes(fixture([row]))
            with self.assertRaises(m.InspectionError):m.inspect_gguf(self.path)

    def test_oversized_counts_and_strings_rejected_without_allocation(self):
        for raw in (b'GGUF'+struct.pack('<IQQ',3,2**63,1),
                    b'GGUF'+struct.pack('<IQQ',3,1,20000),
                    b'GGUF'+struct.pack('<IQQQ',3,1,1,2**63)):
            self.path.write_bytes(raw)
            with self.assertRaises(m.InspectionError):m.inspect_gguf(self.path)

    def test_bad_tensor_offsets_rejected(self):
        for offset in (1,2**63):
            self.path.write_bytes(fixture(offset=offset))
            with self.assertRaises(m.InspectionError):m.inspect_gguf(self.path)

    def test_relative_directory_and_missing_file_rejected(self):
        for path in (Path('fixture.gguf'),self.path.parent,self.path):
            with self.assertRaises((m.InspectionError,OSError)):m.inspect_gguf(path)

    def test_hard_link_alias_is_rejected(self):
        self.path.write_bytes(fixture());alias=self.path.with_name('alias.gguf')
        os.link(self.path,alias)
        with self.assertRaises(m.InspectionError):m.inspect_gguf(alias)

    def test_symlink_is_rejected_when_platform_allows_creation(self):
        self.path.write_bytes(fixture());alias=self.path.with_name('alias.gguf')
        try:alias.symlink_to(self.path)
        except OSError:self.skipTest('Symlink creation unavailable for this user')
        with self.assertRaises(m.InspectionError):m.inspect_gguf(alias)

    def test_cancel_does_not_return_a_success_receipt(self):
        self.path.write_bytes(fixture());event=threading.Event();event.set()
        with self.assertRaisesRegex(m.InspectionError,'cancelled'):m.inspect_gguf(self.path,cancel_event=event)

    def test_monotonic_time_budget_cannot_be_renewed(self):
        self.path.write_bytes(fixture())
        with patch.object(m.time,'monotonic',side_effect=[100,106]):
            with self.assertRaisesRegex(m.InspectionError,'time budget'):m.inspect_gguf(self.path)

    def test_change_during_read_invalidates_identity(self):
        self.path.write_bytes(fixture());original=m._Reader.string;changed=[]
        def mutate(reader,*args):
            result=original(reader,*args)
            if not changed:
                with self.path.open('ab') as file:file.write(b'changed')
                changed.append(True)
            return result
        with patch.object(m._Reader,'string',mutate):
            with self.assertRaisesRegex(m.InspectionError,'changed'):m.inspect_gguf(self.path)

    def test_repeated_same_path_writes_match_windows_handle_creation_identity(self):
        for count in range(10):
            self.path.write_bytes(fixture(tail=1024+count))
            before=self.path.stat()
            with self.path.open('rb') as file:
                self.assertEqual(m.identity(before),m.identity(os.fstat(file.fileno())))
            self.assertEqual(m.inspect_gguf(self.path)['fileSizeBytes'],self.path.stat().st_size)


if __name__=='__main__':unittest.main()
