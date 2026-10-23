"""Model-neutral metadata, behavior and lifecycle facade for EngineManager.

No model loader, catalog downloader or compatibility authority lives here.
Declared GGUF metadata is not native evidence. Resource estimates are advisory,
and the existing independent GPT probe policy remains unchanged.
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
import threading
import os
from typing import Protocol, Any
import json
import re
from types import MappingProxyType
from collections.abc import Mapping

GIB=1024**3


def gguf_quantization_label(value):
    return {0:'F32',1:'F16',2:'Q4_0',3:'Q4_1',7:'Q8_0',14:'Q4_K_S',15:'Q4_K_M',16:'Q5_K_S',
            17:'Q5_K_M',18:'Q6_K',32:'BF16',38:'MXFP4'}.get(value,'Unknown GGUF type') if type(value) is int else 'Unknown'


def model_display_name(declared_name, file_type):
    name=''.join(c for c in str(declared_name) if c.isprintable())[:160]
    quantization=gguf_quantization_label(file_type)
    if re.search(r'\b(?:awq|gptq)\b',name,re.IGNORECASE) and not quantization.startswith('Unknown'):
        # Keep the raw header label separately. AWQ/GPTQ in a declared name is
        # not the quantization identity of this GGUF file.
        name=re.sub(r'\b(?:awq|gptq)\b','',name,flags=re.IGNORECASE)
        name=' '.join(name.split()).strip(' -_')+' · GGUF '+quantization
    return name


def message_visible_text(content,metrics=None):
    """Use the turn's recorded identity, never the newly selected model."""
    from integrated_runtime import visible_assistant_text
    try:metrics=json.loads(metrics) if isinstance(metrics,str) else metrics
    except (TypeError,ValueError):metrics={}
    identity=metrics.get('model_identity',{}) if isinstance(metrics,dict) else {}
    architecture=identity.get('architecture') if isinstance(identity,dict) else None
    return visible_assistant_text(content,architecture=architecture)


def engine_visible_text(engine,content):
    from integrated_runtime import visible_assistant_text
    behavior=getattr(getattr(engine,'runtime',None),'behavior',None)
    architecture=behavior.family.value if isinstance(behavior,ModelBehavior) else None
    return visible_assistant_text(content,architecture=architecture)


class Family(str,Enum):
    GPT_OSS='gpt-oss'
    QWEN3='qwen3'
    QWEN35='qwen35'
    LLAMA='llama'
    GEMMA='gemma'
    OTHER='other'


def positive(value):return value if type(value) is int and 0<value<=2**63-1 else None


def _freeze(value):
    if isinstance(value,dict):return MappingProxyType({k:_freeze(v) for k,v in value.items()})
    if isinstance(value,list):return tuple(_freeze(v) for v in value)
    return value


@dataclass(frozen=True)
class ModelInfo:
    architecture:str
    display_name:str
    file_bytes:int
    context_limit:int|None
    template_sha256:str|None
    header_sha256:str
    observation:tuple
    metadata:Mapping
    warnings:tuple
    stored_tensor_elements:int|None=None

    @property
    def family(self):
        if self.architecture=='gpt-oss':return Family.GPT_OSS
        if self.architecture in {'qwen3','qwen3moe'}:return Family.QWEN3
        if self.architecture in {'qwen35','qwen35moe'}:return Family.QWEN35
        if self.architecture=='llama':return Family.LLAMA
        if self.architecture in {'gemma','gemma2','gemma3','gemma3n'}:return Family.GEMMA
        return Family.OTHER

    @classmethod
    def from_inspection(cls,result):
        m=dict(result['metadata']);arch=m.get('general.architecture','')
        arch=arch if isinstance(arch,str) else ''
        name=m.get('general.name',arch or 'Unidentified local model')
        name=model_display_name(name,m.get('general.file_type'))
        template=m.get('tokenizer.chat_template',{})
        return cls(arch,name,int(result['fileSizeBytes']),positive(m.get(arch+'.context_length')),
                   template.get('sha256') if isinstance(template,dict) else None,
                   result['headerMetadataSha256'],tuple(result['fileObservation']),_freeze(m),tuple(result.get('warnings',())),positive(result.get('storedTensorElements')))

    def capability_report(self):
        return {'family':self.family.value,'architecture':self.architecture,'name':self.display_name,
                'contextMetadata':self.context_limit,'fileBytes':self.file_bytes,
                'storedTensorElements':self.stored_tensor_elements,'declaredParameterCount':positive(self.metadata.get('general.parameter_count')),
                'declared':{'name':self.metadata.get('general.name'),'chatTemplate':bool(self.template_sha256),'tokenizer':self.metadata.get('tokenizer.ggml.model'),
                            'quantizationFileType':self.metadata.get('general.file_type'),'splitCount':self.metadata.get('split.count',1)},
                'backendSupported':'NOT_ESTABLISHED_FOR_THIS_EXACT_BINARY',
                'executionVerified':[],'nativeCompatible':False,'identityScope':'header-and-file-observation; tensor integrity not verified',
                'warnings':list(self.warnings)}


@dataclass(frozen=True)
class ModelBehavior:
    family:Family=Family.GPT_OSS  # Explicit backwards-compatible legacy adapter.
    model_alias:str='gpt-oss-20b-local'
    provenance:str='legacy_existing_configuration'
    thinking_branch_observed:bool=False

    @classmethod
    def from_model(cls,info):
        template=info.metadata.get('tokenizer.chat_template',{})
        return cls(info.family,info.display_name or info.architecture,'inspected_metadata_not_native_acceptance',
                   template.get('enableThinkingBranchObserved') is True if isinstance(template,Mapping) else False)

    @property
    def harmony(self):return self.family is Family.GPT_OSS

    def template_kwargs(self,effort=None):
        # Use only the observed Qwen3 template branch. Low-effort Agent/finalization requests
        # must reserve their bounded output for an answer instead of hidden text.
        if self.family is Family.QWEN3 and self.thinking_branch_observed:
            # Standard/automatic requests have a finite answer budget, not a
            # supported Qwen thinking-token budget. Keep that budget visible;
            # explicit Deep/high remains the opt-in thinking mode.
            return {'enable_thinking':effort=='high'}
        return {'reasoning_effort':effort if effort in {'low','medium','high'} else 'low'} if self.harmony else {}

    def generation_options(self,effort,output):
        if not self.harmony:
            kwargs=self.template_kwargs(effort)
            return {'chat_template_kwargs':kwargs} if kwargs else {}
        return {'chat_template_kwargs':self.template_kwargs(effort),
                'reasoning_budget_tokens':min(output//3,{'low':0,'medium':256,'high':512}.get(effort,128))}


class TemplateContentStream:
    """Filter explicit think spans, without interpreting Harmony role tokens.

    The server owns templates/EOS/special tokens and separates structured
    reasoning fields. Explicit think spans are additionally suppressed here.
    Literal <think> examples are conservatively suppressed too; not semantic
    reasoning classification. A truncated hidden span never becomes visible.
    """
    def __init__(self):self.pending='';self.depth=0;self.hidden=False
    def feed(self,text):
        pending=self.pending+text;out=[];offset=0
        while offset<len(pending):
            lower=pending[offset:offset+8].lower()
            if lower.startswith('<think>'):
                self.depth+=1;self.hidden=True;offset+=7;continue
            if lower.startswith('</think>'):
                self.depth=max(0,self.depth-1);offset+=8;continue
            if '<think>'.startswith(lower) or '</think>'.startswith(lower):break
            next_tag=pending.find('<',offset+1 if pending[offset]=='<' else offset)
            end=next_tag if next_tag>=0 else len(pending)
            if not self.depth:out.append(pending[offset:end])
            offset=end
        self.pending=pending[offset:]
        return ''.join(out)
    def finish(self):
        # Do not flush an ambiguous partial opening/closing control tag.
        self.pending='';return ''


def estimate_memory(info,context,*,available_ram,available_vram=0,total_ram=None,profile='balanced',custom_options=None):
    """Conservative advisory envelope, not allocator demand or load permission."""
    if profile not in {'eco','balanced','performance','custom'}:raise ValueError('Unknown hardware profile')
    if not positive(context) or any(type(v) is not int or v<0 for v in (available_ram,available_vram)):
        raise ValueError('Finite nonnegative byte observations and positive context required')
    arch=info.architecture;m=info.metadata
    target=min(context,info.context_limit or context,32768)
    if profile=='eco':
        target=min(target,2048)
    elif profile=='balanced':
        target=min(target,4096)
    elif profile=='performance':
        target=min(target,8192)
    elif profile=='custom' and isinstance(custom_options,dict) and 'context' in custom_options:
        target=min(int(custom_options['context']),info.context_limit or 32768,32768)

    layers=positive(m.get(arch+'.block_count'));embedding=positive(m.get(arch+'.embedding_length'))
    heads=positive(m.get(arch+'.attention.head_count'));kv_heads=positive(m.get(arch+'.attention.head_count_kv'))
    key=positive(m.get(arch+'.attention.key_length'));value=positive(m.get(arch+'.attention.value_length'))
    if not key and embedding and heads and embedding%heads==0:key=embedding//heads
    if not value:value=key
    # Recurrent/hybrid/MoE allocators need their own measured contract. Never
    # assume a dense KV formula represents GPT-OSS or Qwen3.5 memory demands.
    dense=arch in {'llama','qwen3','gemma','gemma2','gemma3'}
    kv=2*target*layers*kv_heads*(key+value) if dense and all((layers,kv_heads,key,value)) else None
    reserve=max(GIB,int((total_ram or 0)*.08))
    overhead=768*1024**2
    cpu=info.file_bytes+overhead+reserve+(kv or 0)

    # GPU offload capacity based on available VRAM
    vram_reserve = GIB
    usable_vram = max(0, available_vram - vram_reserve)
    gpu_layers_possible = 0
    gpu_vram_consumed = 0
    host_ram_reduction = 0
    if layers and info.file_bytes > 0:
        bytes_per_layer = info.file_bytes / (layers + 2)
        if profile == 'eco':
            vram_target = usable_vram * 0.5
            gpu_layers_possible = min(layers, int(vram_target / bytes_per_layer)) if bytes_per_layer > 0 else 0
        elif profile == 'balanced':
            vram_target = usable_vram * 0.8
            gpu_layers_possible = min(layers, int(vram_target / bytes_per_layer)) if bytes_per_layer > 0 else 0
        elif profile == 'performance':
            gpu_layers_possible = min(layers, int(usable_vram / bytes_per_layer)) if bytes_per_layer > 0 else 0
        elif profile == 'custom' and isinstance(custom_options, dict) and 'gpu_layers' in custom_options:
            custom_layers = custom_options['gpu_layers']
            gpu_layers_possible = layers if custom_layers in ('all', -1) else min(layers, max(0, int(custom_layers)))
        else:
            gpu_layers_possible = min(layers, int(usable_vram / bytes_per_layer)) if bytes_per_layer > 0 else 0

        gpu_vram_consumed = int(gpu_layers_possible * bytes_per_layer)
        host_ram_reduction = gpu_vram_consumed

    hybrid_cpu = max(overhead + reserve, cpu - host_ram_reduction)
    hybrid_likely_fits = (hybrid_cpu <= available_ram) if kv is not None else None

    cpu_cores = os.cpu_count() or 4
    if profile == 'eco':
        threads = max(1, min(2, cpu_cores - 1))
        batch = 32
    elif profile == 'balanced':
        threads = max(1, min(4, cpu_cores - 1))
        batch = 64
    elif profile == 'performance':
        threads = max(1, min(8, cpu_cores - 1))
        batch = 128
    elif profile == 'custom' and isinstance(custom_options, dict):
        threads = int(custom_options.get('threads', max(1, min(4, cpu_cores - 1))))
        batch = int(custom_options.get('batch', 64))
    else:
        threads = max(1, min(4, cpu_cores - 1))
        batch = 64

    return {'status':'ADVISORY_ONLY' if kv is not None else 'INCOMPLETE_ALLOCATION_MODEL','context':target,
            'weightFileBytes':info.file_bytes,'kvEstimateBytes':kv,'runtimeOverheadAllowanceBytes':overhead,'systemReserveBytes':reserve,
            'cpuEnvelopeBytes':cpu if kv is not None else None,'cpuLikelyFits':cpu<=available_ram if kv is not None else None,
            'vramHeadroomAfterReserveBytes':usable_vram,
            'gpu_layers_possible':gpu_layers_possible,
            'gpu_vram_consumed_bytes':gpu_vram_consumed,
            'host_ram_reduction_bytes':host_ram_reduction,
            'hybrid_cpu_envelope_bytes':hybrid_cpu if kv is not None else None,
            'hybrid_likely_fits':hybrid_likely_fits,
            'automaticLoadAuthorized':False,
            'limitations':['File bytes are not resident/committed allocation.','CUDA offload, tensor copies, graph buffers and recurrent states need measured backend evidence.'],
            'threads':threads,
            'batch':batch,'profile':profile}


class ManagedTextRuntime(Protocol):
    def describe(self)->dict:...
    def load(self,*,progress:Any,cancel_event:threading.Event|None=None)->str:...
    def generate(self,messages:list[dict],**options)->Any:...
    def count_tokens(self,messages:list[dict])->int:...
    def cancel(self,cancel_event:threading.Event)->None:...
    def unload(self)->None:...


class EngineTextRuntime:
    """Typed lifecycle facade; all loading/resource ownership stays in manager."""
    def __init__(self,manager):self.manager=manager
    def describe(self):return self.manager.chat_status()
    def load(self,*,progress=lambda _:None,cancel_event=None,model=None):return self.manager.ensure_chat(progress,model=model,cancel_event=cancel_event)
    def generate(self,messages,**options):return self.manager.chat_completion(messages,**options)
    def count_tokens(self,messages):
        runtime=self.manager.runtime
        if not runtime or not runtime.ready:raise RuntimeError('Load the selected local model before counting its template tokens.')
        return runtime._prompt_tokens(messages)
    def cancel(self,cancel_event):cancel_event.set()
    def unload(self):
        with self.manager.resource_lock,self.manager.lock:
            if self.manager._active_inference_count:raise RuntimeError('Cancel and finish the active generation before unloading.')
            self.manager._unload_chat(reason='Explicit model-independent unload')
