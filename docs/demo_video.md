# Demo Video — AegisFlow on Studio Next (chain 61997)

**Public URL (no login):**
https://faisalnugroho.github.io/aegisflow/demo-video.html

Direct MP4: https://faisalnugroho.github.io/aegisflow/aegisflow_demo_61997.mp4

## What it shows (all real, nothing staged)

Recorded from the production dApp bundle (the exact `frontend/` served by
GitHub Pages) driving a live run against the AegisFlow Intelligent Contract
deployed on the GenLayer Studio development preview:

| Item | Value |
| --- | --- |
| Network | Studio Next — `studio-dev.genlayer.com/api`, chain ID **61997** |
| Contract | `0xadF028749733F1D5FA73Ffc0532a48e6Ee5A6B82` |
| Explorer | https://explorer-studio-dev.genlayer.com/address/0xadF028749733F1D5FA73Ffc0532a48e6Ee5A6B82 |
| Scenario | S1 — 25 USDC infrastructure payment (safe payment) |
| Consensus | `run_consensus` → **APPROVE** (`policy_compliant`, risk 15) |
| Enforcement | `execute_action` → **EXECUTED** |

The video walks through: landing page → live dashboard (state read from
61997) → Settings screen showing the network/chain/contract and the pinned
evidence registry → scenario submission with an in-browser burner wallet →
consensus in progress (validators fetch the keccak-verified registry and
judge the three semantic criteria) → APPROVE → deterministic enforcement
re-verifying hash/policy/budget → EXECUTED.

## Reproducibility

Every step shown is a real transaction. The corresponding action in the video
is ACT-000010 (consensus APPROVE, executed); its tx hashes and all other
deployment transactions are listed in
`docs/deployment_log_studio_next_61997.json`. Anyone can re-run the same flow
from the public dApp and click through every tx on the explorer.

## Rebuilding the video

Frames: `python3` + Playwright capturing the real UI (screenshots at each
step). Assembly: ffmpeg with burned-in captions (see the captions table in
the repository history) + a TTS narration track. The video is fully
regenerable from the live dApp — no manual mockups, no edited results.
