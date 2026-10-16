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

GIB = 1024 ** 3
GPT_NATIVE_PROBE_MIN_RAM = 15 * GIB // 2


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
