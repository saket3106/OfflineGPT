"""Bounded read-only GGUF metadata inspection; never a model-load approval.

Format reference: https://github.com/ggml-org/ggml/blob/master/docs/gguf.md
Only the caller-selected local file is read, no directory discovery or network.
Tensor data is deliberately not read, hashed, executed or declared valid.
"""
import hashlib
import math
import os
from pathlib import Path
import re
import stat
import struct
import time


class InspectionError(ValueError):
    pass


def ordinary_file(path):
    path=Path(path)
    if not path.is_absolute() or '..' in path.parts or str(path).startswith(('\\\\','//')):
        raise InspectionError('Select an absolute local model file')
    if os.name=='nt' and ':' in str(path)[2:]:
        raise InspectionError('Device paths and alternate data streams are unsupported')
    for part in (path,*path.parents):
        info=part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400:
            raise InspectionError('Model links/reparse paths require separate review')
    info=path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:
        raise InspectionError('Select an ordinary file without mutable hard-link aliases')
    return path,info


def identity(info):
    # Windows stat and fstat expose different legacy ctime semantics in Python
    # 3.12. Compare explicit creation time, not that ambiguous legacy field.
    if os.name=='nt' and not hasattr(info,'st_birthtime_ns'):
        raise InspectionError('Explicit Windows file creation identity unavailable')
    stamp=info.st_birthtime_ns if os.name=='nt' else info.st_ctime_ns
    return (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,stamp)


class _Reader:
    FORMATS={0:'B',1:'b',2:'H',3:'h',4:'I',5:'i',6:'f',7:'B',10:'Q',11:'q',12:'d'}
    def __init__(self,stream,size,cancel_event):
        self.stream=stream;self.size=size;self.cancel=cancel_event
        self.started=time.monotonic();self.consumed=0;self.digest=hashlib.sha256();self.values=0

    def read(self,count):
        if self.cancel is not None and self.cancel.is_set():raise InspectionError('Model inspection cancelled')
        if time.monotonic()-self.started>5:raise InspectionError('Model inspection time budget exceeded')
        if count<0 or count>4*1024**2 or self.consumed+count>32*1024**2:
            raise InspectionError('Model metadata exceeds inspection byte budget')
        data=self.stream.read(count)
        if len(data)!=count:raise InspectionError('Truncated GGUF metadata')
        self.consumed+=count;self.digest.update(data);return data

    def number(self,fmt):return struct.unpack('<'+fmt,self.read(struct.calcsize(fmt)))[0]

    def string(self,limit=4*1024**2):
        count=self.number('Q')
        if count>limit:raise InspectionError('GGUF string exceeds inspection limit')
        try:return self.read(count).decode('utf-8')
        except UnicodeError as error:raise InspectionError('Invalid GGUF UTF-8 string') from error

    def value(self,kind,depth=0):
        self.values+=1
        if self.values>1000000:raise InspectionError('Too many GGUF metadata values')
        if kind==8:return self.string()
        if kind==9:
            if depth>=4:raise InspectionError('Nested metadata exceeds inspection depth')
            item_type=self.number('I');count=self.number('Q')
            if item_type not in {*self.FORMATS,8,9} or count>1000000:
                raise InspectionError('Unsupported or oversized GGUF array')
            # Discard values as they are read; never allocate an entire vocabulary.
            for _ in range(count):self.value(item_type,depth+1)
            return {'arrayType':item_type,'count':count}
        if kind not in self.FORMATS:raise InspectionError('Unknown GGUF metadata type')
        value=self.number(self.FORMATS[kind])
        if kind==7 and value not in (0,1):raise InspectionError('Invalid GGUF boolean')
        if kind in (6,12) and not math.isfinite(value):raise InspectionError('Non-finite GGUF metadata')
        return bool(value) if kind==7 else value


def inspect_gguf(path,*,cancel_event=None):
    """Inspect metadata only. The result can never enable an inference backend."""
    path,before=ordinary_file(path)
    selected={};keys=set();tensor_names=set();types=set();offsets=[];stored_elements=0
    with path.open('rb') as stream:
        if identity(os.fstat(stream.fileno()))!=identity(before):raise InspectionError('Selected model changed before opening')
        r=_Reader(stream,before.st_size,cancel_event)
        if r.read(4)!=b'GGUF':raise InspectionError('Not a GGUF file')
        version=r.number('I')
        if version not in (2,3):raise InspectionError('Unsupported GGUF version or byte order')
        tensors=r.number('Q');metadata_count=r.number('Q')
        if tensors>131072 or metadata_count>16384:raise InspectionError('GGUF header count exceeds inspection limits')
        for _ in range(metadata_count):
            key=r.string(65535)
            if not re.fullmatch(r'[a-z0-9_\-]+(?:\.[a-z0-9_\-]+)*',key) or key in keys:
                raise InspectionError('Invalid or duplicate GGUF metadata key')
            keys.add(key);value=r.value(r.number('I'))
            if key=='general.name' and not isinstance(value,str):raise InspectionError('Model display name must be a string')
            if key in {'general.name','general.architecture','general.file_type','general.quantization_version','general.alignment',
                       'general.license','general.parameter_count','general.size_label','tokenizer.ggml.model','tokenizer.ggml.tokens','split.count','split.no'} or key.endswith(('.context_length','.block_count','.embedding_length','.attention.head_count','.attention.head_count_kv','.attention.key_length','.attention.value_length','.full_attention_interval')):
                selected[key]=value
            elif key=='tokenizer.chat_template':
                if not isinstance(value,str):raise InspectionError('Chat template must be a string')
                # Observe only the embedded template's explicit control branch;
                # never execute it here or grant native compatibility from it.
                thinking_control=bool(re.search(r'{%-?\s*if\s+enable_thinking\s+is\s+defined\s+and\s+enable_thinking\s+is\s+false\s*-?%}',value))
                selected[key]={'sha256':hashlib.sha256(value.encode()).hexdigest(),'characters':len(value),'trusted':False,
                               'enableThinkingBranchObserved':thinking_control}
        alignment=selected.get('general.alignment',32)
        if type(alignment) is not int or alignment<1 or alignment>4096 or alignment&(alignment-1):
            raise InspectionError('Invalid or unsupported GGUF tensor alignment')
        for _ in range(tensors):
            name=r.string(128)
            if not name or name in tensor_names:raise InspectionError('Empty or duplicate tensor name')
            tensor_names.add(name);dimensions=r.number('I')
            if dimensions<1 or dimensions>4:raise InspectionError('Unsupported GGUF tensor dimensionality')
            elements=1
            for _ in range(dimensions):
                size=r.number('Q')
                if size==0 or size>2**63-1:raise InspectionError('Invalid tensor dimension')
                elements*=size
                if elements>2**63-1:raise InspectionError('Tensor element count exceeds inspection arithmetic bound')
            stored_elements+=elements
            if stored_elements>2**63-1:raise InspectionError('Stored tensor elements exceed inspection arithmetic bound')
            types.add(r.number('I'));offset=r.number('Q')
            if offset%alignment:raise InspectionError('Unaligned tensor offset')
            offsets.append(offset)
        data_start=(r.consumed+alignment-1)//alignment*alignment
        if tensors and (data_start>=before.st_size or any(data_start+v>=before.st_size for v in offsets)):
            raise InspectionError('Tensor offsets exceed selected file')
        if identity(os.fstat(stream.fileno()))!=identity(before):raise InspectionError('Selected model changed during inspection')
        _,after=ordinary_file(path)
        if identity(after)!=identity(before):raise InspectionError('Selected model path changed during inspection')
    architecture=selected.get('general.architecture')
    tokenizer=selected.get('tokenizer.ggml.model');tokens=selected.get('tokenizer.ggml.tokens',{})
    warnings=[]
    if not isinstance(architecture,str) or not architecture.strip():warnings.append('Architecture metadata missing or invalid')
    if not isinstance(tokenizer,str) or not tokenizer.strip() or not isinstance(tokens,dict) or tokens.get('arrayType')!=8 or not tokens.get('count'):
        warnings.append('Tokenizer metadata incomplete; this is not a usable-model approval')
    if not selected.get('tokenizer.chat_template',{}).get('characters'):warnings.append('Chat template absent; no prompt format may be assumed')
    if selected.get('split.count',1)!=1:warnings.append('Sharded model requires separate complete-asset review')
    return {'status':'STATIC_METADATA_INSPECTED','version':version,'metadata':selected,'tensorCount':tensors,
            'tensorTypeIds':sorted(types),'fileSizeBytes':before.st_size,'bytesRead':r.consumed,
            'storedTensorElements':stored_elements,
            'headerMetadataSha256':r.digest.hexdigest(),'fullModelSha256':None,
            'tensorDataIntegrity':'NOT_VERIFIED','nativeCompatible':False,'warnings':warnings,
            'elapsedSeconds':time.monotonic()-r.started,'fileObservation':identity(before)}
