"""gltest direct-mode pytest fixtures for AegisFlow.

setup_sdk_paths MUST be given the contract path (Sep 2026 lesson): it
parses the header pin (py-genlayer:1jb45...) and resolves the matching
OLD runner + std-lib deterministically. Without it, the newest runner is
picked, whose std-lib dropped allow_storage — NameError on clean CI.
"""
from pathlib import Path

from gltest.direct.sdk_loader import setup_sdk_paths

setup_sdk_paths(Path(__file__).resolve().parent.parent / "contracts"
                / "AegisFlow.py")
