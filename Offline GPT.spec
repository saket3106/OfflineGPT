# -*- mode: python ; coding: utf-8 -*-


from PyInstaller.utils.hooks import collect_all
from runtime_contracts import validate_windows_payload_paths
voice_data=[];voice_binaries=[];voice_imports=[]
for package in ['kokoro_onnx','espeakng_loader','phonemizer','onnxruntime']:
    data,binary,hidden=collect_all(package)
    voice_data+=data;voice_binaries+=binary;voice_imports+=hidden

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=voice_binaries,
    datas=[('ui', 'ui'), ('assistant_system_prompt.md', '.')]+voice_data,
    hiddenimports=['PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'PySide6.QtWebChannel', 'intelligence', 'router', 'validators', 'math_engine', 'code_engine', 'rag_engine', 'tool_registry', 'memory_manager', 'logic_engine', 'grounding','voice_runtime','PySide6.QtMultimedia','image_pipeline','workspace_services','integrated_runtime','model_inspection']+voice_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
# Qt uses the unversioned Windows ICU ABI. A Poppler directory on the build
# host PATH contains a same-named ICU DLL with versioned exports. Bundling it
# would shadow Windows ICU and break QtCore before the app can show a window.
a.binaries = [entry for entry in a.binaries if not (entry[0].lower() in {'icuuc.dll','icudt78.dll'} and 'poppler' in entry[1].lower())]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Offline GPT',
    icon='frontend-source/public/brand/offline-gpt.ico',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    contents_directory='.',
)
# Fail before COLLECT can resolve duplicates by overwrite order. This protects
# every ordinary scheduled build, without adding unapproved inference binaries.
validate_windows_payload_paths([entry[0] for entry in a.binaries] + [entry[0] for entry in a.datas] + ['Offline GPT.exe'])
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Offline GPT',
)
