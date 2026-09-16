"""gltest direct-mode pytest fixtures for AegisFlow (v0.6-rc5 SDK).

CRITICAL ORDERING (gltest 0.30.0rc2): the genlayer std-lib decides at
import time whether it is "in the VM" by looking for _genlayer_wasi
WITHOUT the FAKE_VM attribute (v0.6 semantics: any _genlayer_wasi
present = in-VM). gltest's own `direct_vm` fixture imports genlayer via
create_address() BEFORE its activate() injects the mock — so the std-lib
ends up IS_IN_VM=False and message.raw stays Ellipsis.

Fix: inject wasi_mock as _genlayer_wasi HERE, before anything imports
genlayer, and pre-add the SDK paths by running setup_sdk_paths against
the contract header pin at conftest import time.
"""
from pathlib import Path

import sys

# 1. resolve SDK paths for the contract pin FIRST (adds std-lib to sys.path)
from gltest.direct.sdk_loader import setup_sdk_paths

setup_sdk_paths(Path(__file__).resolve().parent.parent / "contracts"
                / "AegisFlow.py")

# 2. inject the direct-mode WASI mock BEFORE genlayer is first imported
if "_genlayer_wasi" not in sys.modules:
    from gltest.direct import wasi_mock
    sys.modules["_genlayer_wasi"] = wasi_mock


# 3. v0.6 compat: exec_prompt(response_format='json') expects the WASI layer
# to hand back RAW TEXT (the JSON string); gltest 0.30.0rc2's wasi_mock
# auto-parses JSON strings into dicts, which the v0.6 decoder then rejects
# ("JSON result is not text"). Wrap the handler to return the mock as-is.
from gltest.direct import wasi_mock as _wasi_mock

if not getattr(_wasi_mock, "_aegis_v06_patched", False):
    _orig_llm = _wasi_mock._handle_llm_request

    def _handle_llm_request_raw(vm, data):
        prompt = data.get("prompt", "")
        response = vm._match_llm_mock(prompt)
        if response is not None:
            return {"ok": response}  # no auto-parse: v0.6 decodes text itself
        return _orig_llm(vm, data)

    _wasi_mock._handle_llm_request = _handle_llm_request_raw
    _wasi_mock._aegis_v06_patched = True
