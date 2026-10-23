"""Build-time collection of the pinned optional Agent SDK.

The application can run chat without MCP, but an Agent-enabled package must not
silently omit it. This verifies versions and collects dynamic imports/metadata;
it does not approve wheel origins, native DLL provenance or redistribution.
"""
from __future__ import annotations

import importlib.metadata
from pathlib import Path
import re


class AgentPackagingError(RuntimeError):
    pass


def normalized_name(value):
    return re.sub(r"[-_.]+", "-", value).lower()


def read_agent_lock(path):
    pins = {}
    for number, raw in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([A-Za-z0-9_.+!-]+) --hash=sha256:([a-f0-9]{64})", line)
        if not match:
            raise AgentPackagingError(f"Invalid Agent lock line {number}")
        name, version, digest = match.groups()
        key = normalized_name(name)
        if key in pins:
            raise AgentPackagingError(f"Duplicate Agent lock identity: {key}")
        pins[key] = (name, version, digest)
    from agent_mcp import SDK_VERSION
    if any(pins.get(name, (None, None))[1] != SDK_VERSION for name in ("mcp", "mcp-types")):
        raise AgentPackagingError("Agent lock must pin the runtime's exact MCP and mcp-types versions")
    return pins


def verify_agent_environment(pins, distributions=None):
    installed = {}
    for dist in importlib.metadata.distributions() if distributions is None else distributions:
        name = normalized_name(dist.metadata.get("Name", ""))
        if name in pins:
            installed.setdefault(name, []).append(dist.version)
    findings = []
    for key, (_, version, _) in pins.items():
        actual = installed.get(key, [])
        if actual != [version]:
            findings.append(f"{key}: expected one {version}, found {actual or 'missing'}")
    if findings:
        raise AgentPackagingError("Agent build dependencies are incomplete or ambiguous:\n" + "\n".join(findings))


def collect_agent_inputs(lock_path, *, distributions=None, collectors=None):
    """Fail before Analysis, then use standard PyInstaller collectors/hooks.

    MCP's client/server modules and AnyIO backend are imported dynamically.
    SDK availability also needs dist-info, including licenses, in the payload.
    pywin32's supported PyInstaller hooks collect its native runtime; executing
    an arbitrary .pth file or copying DLLs after sealing is not a build recipe.
    """
    pins = read_agent_lock(lock_path)
    verify_agent_environment(pins, distributions)
    if collectors is None:
        from PyInstaller.utils.hooks import collect_submodules, collect_data_files, copy_metadata
    else:
        collect_submodules, collect_data_files, copy_metadata = collectors
    hidden = collect_submodules("mcp", filter=lambda name: name != "mcp.cli" and not name.startswith("mcp.cli."), on_error="raise")
    hidden += ["anyio._backends._asyncio", "win32api", "win32job", "pywintypes"]
    data = collect_data_files("mcp")
    for name, _, _ in pins.values():
        data += copy_metadata(name)
    return data, [], sorted(set(hidden))
