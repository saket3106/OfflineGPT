"""Model-independent contracts; catalog metadata never grants execution rights.

This module performs no discovery, network access, model loading or code loading.
Native evidence must be approved by the independent engineering acceptance gate,
not supplied by a downloaded catalog or inferred from a model's name.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
import re
from typing import Protocol
from pathlib import PureWindowsPath
import unicodedata

GIB = 1024 ** 3
GPT_NATIVE_PROBE_MIN_RAM = 15 * GIB // 2


class NativePayloadError(ValueError):
    """A native build/release input lacks an unambiguous verified identity."""


def windows_payload_path(value):
    """Conservative portable Windows destination, independent of host OS."""
    if not isinstance(value, str) or not value or any(ord(c) < 32 for c in value):
        raise NativePayloadError('Invalid Windows payload path')
    path = PureWindowsPath(value)
    parts = value.replace('\\', '/').split('/')
    if path.drive or path.root or any(not p or p in {'.', '..'} or ':' in p or p.endswith((' ', '.')) or
        re.fullmatch(r'(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])', p.split('.')[0].rstrip(' .').translate(str.maketrans('¹²³','123'))) or
        any(c in p for c in '<>"|?*') for p in parts):
        raise NativePayloadError('Unsafe or ambiguous Windows payload path')
    if any(re.search(r'~[0-9]+(?:\.|$)', p) for p in parts):
        raise NativePayloadError('DOS short-name payload aliases are unsupported')
    return '/'.join(parts)


def validate_windows_payload_paths(names):
    """Reject duplicates even when bytes match; never choose overwrite order."""
    normalized = []
    seen = set()
    for name in names:
        portable = windows_payload_path(name)
        key = unicodedata.normalize('NFC', portable).casefold()
        if key in seen:
            raise NativePayloadError('Duplicate Windows-equivalent payload path: ' + portable)
        seen.add(key)
        normalized.append(portable)
    for key in seen:
        parts = key.split('/')
        if any('/'.join(parts[:i]) in seen for i in range(1, len(parts))):
            raise NativePayloadError('Payload file conflicts with a parent directory')
    return tuple(normalized)


def validate_native_payload(contract, actual_payload, *, verify_receipt=None, verify_system_library=None):
    """Pure native closure gate; externally verified receipts are mandatory.

    actual_payload maps independently measured relative paths to sha256/bytes.
    Receipt verifiers belong to a trusted build controller, never to downloaded
    metadata. This does not inspect PE imports, grant redistribution rights or
    assemble a package. A controller must independently measure imports (direct,
    delayed and dynamic), verify origins/exports and supply those observations.
    """
    if not callable(verify_receipt) or not callable(verify_system_library):
        raise NativePayloadError('Independent provenance, legal and system-library verification required')
    if not isinstance(contract, dict) or set(contract) != {'version','sourceCommit','platform','profile','files'}:
        raise NativePayloadError('Unknown or incomplete native contract')
    if contract['version'] != 1 or type(contract['version']) is not int or contract['platform'] != 'windows-x64' or not isinstance(contract['profile'],str) or contract['profile'] not in {'cpu','cuda'}:
        raise NativePayloadError('Unsupported native profile')
    if not isinstance(contract['sourceCommit'], str) or not re.fullmatch('[0-9a-f]{40}', contract['sourceCommit']):
        raise NativePayloadError('Exact accepted source identity required')
    if not isinstance(contract['files'], list) or not contract['files'] or not isinstance(actual_payload, dict):
        raise NativePayloadError('Complete independently measured native files required')
    paths = validate_windows_payload_paths(row.get('destination') for row in contract['files'] if isinstance(row, dict))
    if len(paths) != len(contract['files']):
        raise NativePayloadError('Malformed native file record')
    actual_paths = validate_windows_payload_paths(actual_payload)
    measured = {p.casefold(): actual_payload[original] for original,p in zip(actual_payload,actual_paths)}
    by_name = {}
    for row,path in zip(contract['files'],paths):
        required = {'destination','sha256','bytes','origin','version','license','provenanceReceipt','redistributionReceipt','imports','dynamicLoadsReviewed'}
        if set(row) != required:
            raise NativePayloadError('Incomplete or self-authorizing native identity')
        if any(not isinstance(row[k], str) or not row[k].strip() for k in ('origin','version','license')):
            raise NativePayloadError('Missing native origin, version or license')
        for k in ('sha256','provenanceReceipt','redistributionReceipt'):
            if not isinstance(row[k],str) or not re.fullmatch('[0-9a-f]{64}',row[k]):
                raise NativePayloadError('Invalid exact native or approval identity')
        if type(row['bytes']) is not int or row['bytes'] <= 0 or row['dynamicLoadsReviewed'] is not True:
            raise NativePayloadError('Native size or dynamic-loader review missing')
        if measured.get(path.casefold()) != {'sha256':row['sha256'],'bytes':row['bytes']}:
            raise NativePayloadError('Native bytes differ from approved identity: '+path)
        if not isinstance(row['imports'],list):
            raise NativePayloadError('Native import observations required')
        if verify_receipt('provenance',row) is not True or verify_receipt('redistribution',row) is not True:
            raise NativePayloadError('Native provenance or redistribution is not independently approved')
        base=path.rsplit('/',1)[-1].casefold()
        if base in by_name:
            raise NativePayloadError('Ambiguous native loader basename across directories')
        by_name[base]=row
    native_actual={p.casefold() for p in actual_paths if p.lower().endswith(('.exe','.dll','.pyd'))}
    if native_actual != {p.casefold() for p in paths}:
        raise NativePayloadError('Unapproved or missing executable native payload')
    for row in contract['files']:
        for dependency in row['imports']:
            if not isinstance(dependency,dict) or set(dependency)!={'library','kind'} or dependency['kind'] not in {'direct','delay','dynamic'}:
                raise NativePayloadError('Unmeasured native import kind')
            name=windows_payload_path(dependency['library'])
            if '/' in name:
                raise NativePayloadError('Native dependency must have one loader basename')
            if name.casefold() not in by_name and verify_system_library(name,dependency['kind']) is not True:
                raise NativePayloadError('Unresolved native dependency: '+name)
    has_cuda=any(n.startswith(('cudart','cublas','ggml-cuda')) for n in by_name)
    if has_cuda != (contract['profile']=='cuda'):
        raise NativePayloadError('CPU/CUDA profile does not match native payload')
    return {'status':'STATIC_CONTRACT_VERIFIED','profile':contract['profile'],'nativeFiles':len(paths),
            'nativeInferenceVerified':False,'cleanWindowsVerified':False,
            'scope':'Externally approved hash/import contract only; real loader and application acceptance remain required'}


class CompatibilityError(ValueError):
    pass


class Capability(str, Enum):
    TEXT = "text"
    STREAMING = "streaming"
    TOOLS = "tools"
    JSON = "structured-json"
    VISION = "vision"


class EvidenceScope(str, Enum):
    DETERMINISTIC = "deterministic"
    PACKAGED_SHELL = "packaged-shell"
    NATIVE = "native"


def _digest(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        raise CompatibilityError("Exact SHA-256 identity is required")


def _positive_int(value, label, allow_zero=False):
    if type(value) is not int or value < (0 if allow_zero else 1):
        raise CompatibilityError(label + " must be a bounded integer")


@dataclass(frozen=True)
class ModelContract:
    model_id: str
    architecture: str
    quantization: str
    model_sha256: str
    runtime_id: str
    runtime_sha256: str
    template_sha256: str
    context_limit: int
    required_assets: tuple[str, ...]
    license_id: str

    def __post_init__(self):
        for field in (self.model_id, self.architecture, self.quantization, self.runtime_id, self.license_id):
            if not isinstance(field, str) or not field.strip() or len(field) > 256 or any(ord(c) < 32 for c in field):
                raise CompatibilityError("Invalid model contract label")
        for value in (self.model_sha256, self.runtime_sha256, self.template_sha256):
            _digest(value)
        _positive_int(self.context_limit, "Context")
        if self.context_limit > 1048576:
            raise CompatibilityError("Context exceeds the contract schema limit")
        if type(self.required_assets) is not tuple or not self.required_assets or len(set(self.required_assets)) != len(self.required_assets):
            raise CompatibilityError("Immutable, unique required asset identities are required")
        for value in self.required_assets:
            _digest(value)
        if self.model_sha256 not in self.required_assets:
            raise CompatibilityError("Required assets must include the primary model")


@dataclass(frozen=True)
class HardwareSnapshot:
    # Identity includes backend/driver/CPU characteristics; a GPU name is insufficient.
    identity_sha256: str
    available_ram: int | None
    available_vram: int | None
    free_disk: int | None
    gpu_temperature_c: float | None = None

    def __post_init__(self):
        _digest(self.identity_sha256)
        for value in (self.available_ram, self.available_vram, self.free_disk):
            if value is not None:
                _positive_int(value, "Available resource", True)
        if self.gpu_temperature_c is not None and (type(self.gpu_temperature_c) not in (int, float) or not math.isfinite(self.gpu_temperature_c)):
            raise CompatibilityError("Invalid temperature observation")


@dataclass(frozen=True)
class ResourcePolicy:
    min_ram: int
    min_vram: int
    min_disk: int

    def __post_init__(self):
        _positive_int(self.min_ram, "RAM")
        _positive_int(self.min_vram, "VRAM", True)
        _positive_int(self.min_disk, "Disk", True)

    def blockers(self, hardware, *, gpt_native_probe=False):
        ram = max(self.min_ram, GPT_NATIVE_PROBE_MIN_RAM) if gpt_native_probe else self.min_ram
        limits = (("RAM", hardware.available_ram, ram), ("VRAM", hardware.available_vram, self.min_vram), ("disk", hardware.free_disk, self.min_disk))
        reasons = [name + (" availability is unknown" if value is None else " is below the validated requirement")
                   for name, value, minimum in limits if minimum and (value is None or value < minimum)]
        if gpt_native_probe and (hardware.gpu_temperature_c is None or hardware.gpu_temperature_c >= 70):
            reasons.append("GPT native probe requires an observed GPU temperature below 70 C")
        return tuple(reasons)


@dataclass(frozen=True)
class ValidationEvidence:
    contract: ModelContract
    hardware_sha256: str
    scope: EvidenceScope
    capabilities: frozenset[Capability]
    tested_context: int
    receipt_sha256: str
    policy: ResourcePolicy

    def __post_init__(self):
        if type(self.contract) is not ModelContract or type(self.policy) is not ResourcePolicy or type(self.scope) is not EvidenceScope:
            raise CompatibilityError("Typed contract, resource policy and evidence scope required")
        _digest(self.hardware_sha256)
        _digest(self.receipt_sha256)
        if type(self.capabilities) is not frozenset or not self.capabilities or any(type(c) is not Capability for c in self.capabilities):
            raise CompatibilityError("Explicit immutable capability evidence required")
        _positive_int(self.tested_context, "Tested context")
        if self.tested_context > self.contract.context_limit:
            raise CompatibilityError("Test evidence exceeds the model context contract")


class CompatibilityRegistry:
    """In-memory reviewed evidence only; not a deserializer for model metadata.

    The verifier is an application-owned independent acceptance service. A true
    return records that review, not cryptographic proof of model correctness.
    Persistence, authenticated receipt import and UI onboarding remain separate.
    """
    def __init__(self, verifier):
        if not callable(verifier):
            raise CompatibilityError("Independent evidence verifier is required")
        self._verifier = verifier
        self._entries = {}

    def register(self, evidence):
        if type(evidence) is not ValidationEvidence:
            raise CompatibilityError("Untrusted catalog records cannot grant capabilities")
        if self._verifier(evidence) is not True:
            raise CompatibilityError("Independent evidence acceptance failed")
        key = (evidence.contract, evidence.hardware_sha256)
        if key in self._entries:
            raise CompatibilityError("Evidence replacement requires explicit review, not silent overwrite")
        self._entries[key] = evidence

    def assess(self, contract, hardware, capabilities, context, *, gpt_native_probe=False):
        if type(contract) is not ModelContract or type(hardware) is not HardwareSnapshot:
            raise CompatibilityError("Exact contract and hardware observations required")
        if type(capabilities) is not frozenset or not capabilities or any(type(c) is not Capability for c in capabilities):
            raise CompatibilityError("Explicit required capabilities are required")
        _positive_int(context, "Requested context")
        evidence = self._entries.get((contract, hardware.identity_sha256))
        if evidence is None or evidence.scope is not EvidenceScope.NATIVE:
            return {"status": "NOT_VERIFIED", "allowed": False, "reasons": ("No accepted native evidence for these exact inputs and hardware",)}
        reasons = []
        if not capabilities <= evidence.capabilities:
            reasons.append("Requested capabilities lack independent native acceptance")
        if context > evidence.tested_context:
            reasons.append("Requested context exceeds independently tested context")
        reasons.extend(evidence.policy.blockers(hardware, gpt_native_probe=gpt_native_probe))
        return {"status": "BLOCKED" if reasons else "VERIFIED_FOR_RECORDED_SCOPE", "allowed": not reasons,
                "reasons": tuple(reasons), "receiptSha256": evidence.receipt_sha256}


class TextRuntimeAdapter(Protocol):
    """Resource ownership and cancellation remain with EngineManager."""
    runtime_id: str

    def complete(self, messages, *, max_tokens, temperature, cancel_event, on_token,
                 progress, connection_callback, top_p, allowed_tools, reasoning_effort): ...


class ExistingTextAdapter:
    """Route existing integrations without altering their prompt or lifecycle.

    This does not validate any new architecture, claim tools are reliable for a
    new model, or authorize model imports. New runtime IDs fail closed.
    """
    def __init__(self, manager, backend):
        if backend not in {"bundled", "lmstudio"}:
            raise CompatibilityError("Unsupported text runtime; compatibility acceptance is required")
        self.manager = manager
        self.runtime_id = backend

    def complete(self, messages, *, max_tokens, temperature, cancel_event, on_token,
                 progress, connection_callback, top_p, allowed_tools, reasoning_effort):
        kwargs = dict(connection_callback=connection_callback, top_p=top_p, allowed_tools=allowed_tools,
                      reasoning_effort=(reasoning_effort or "low") if self.runtime_id == "lmstudio" else reasoning_effort)
        if self.runtime_id == "lmstudio":
            return self.manager.lm_runtime.complete(messages, int(max_tokens), float(temperature), cancel_event, on_token, progress, **kwargs)
        runtime = self.manager.runtime
        if not runtime or not runtime.ready:
            raise RuntimeError("Offline GPT's bundled GPT-OSS engine stopped before the response began.")
        return runtime.complete(messages, runtime.context, int(max_tokens), float(temperature), cancel_event,
                                on_token, progress, self.manager.chat_memory_profile, **kwargs)
