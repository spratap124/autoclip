# Claude Review Prompt: AutoClip CS2 Highlight Detection

You are reviewing AutoClip’s CS2 player-specific highlight detector. Do not implement changes. Read the complete audit report at `docs/CS2_HIGHLIGHT_DETECTION_AUDIT.md` and inspect the actual current source files it references before recommending an architecture. Treat the report as an investigation aid, not as unquestionable truth: verify its claims against code and call out any stale, incorrect, incomplete, or unsupported statements.

## Review objectives

1. Critically review the detection architecture from uploaded video through React review candidates.
2. Identify flaws, hidden assumptions, coupled failure modes, and missing observability.
3. Explain why player-specific kills and multi-kills may be missed, distinguishing sampling misses, OCR errors, attacker/victim parsing, identity matching, grouping, round segmentation, candidate scoring, and ranking/truncation.
4. Challenge the current design where the evidence supports doing so. In particular, assess whether strict exact attacker attribution is an appropriate candidate gate, whether the ROI and temporal confirmation design is robust, and whether current round/multi-kill semantics justify their category labels.
5. Recommend a more reliable strategy that prioritizes accuracy per computational cost. Prefer local, explainable techniques and staged processing. Do not recommend expensive AI infrastructure unless you justify its incremental value against simpler alternatives.
6. Propose a measurable validation plan using real CS2 gameplay, including ground-truth schema, recording diversity, train/tune versus held-out separation, event-level and candidate-level metrics, uncertainty review, and acceptance criteria.
7. Do not make code or configuration changes and do not run expensive full-video analysis. Return recommendations and a staged plan only; implementation requires the user’s approval.

## Repository paths to inspect

- `backend/autoclip/pipeline/cs2/models.py`
- `backend/autoclip/pipeline/cs2/sampling.py`
- `backend/autoclip/pipeline/cs2/recognition.py`
- `backend/autoclip/pipeline/cs2/candidates.py`
- `backend/autoclip/pipeline/cs2/detector.py`
- `backend/autoclip/pipeline/runner.py`
- `backend/autoclip/pipeline/ingest.py`
- `backend/autoclip/api/sources.py`
- `backend/autoclip/api/jobs.py`
- `backend/autoclip/api/schemas.py`
- `frontend/src/pages/Ingest.tsx`
- `frontend/src/pages/Review.tsx`
- `tests/test_cs2.py`
- `tests/test_cs2_recognition.py`
- `tests/fixtures/cs2_ground_truth_E04_f2nCTnE.json`

The audit also describes read-only cached evidence. Treat it as anecdotal unless you can verify its provenance and relationship to labels; do not report it as a dataset-wide accuracy result.

## Requested response format

1. **Independent summary:** What the detector demonstrably does today.
2. **Audit corrections:** Any inaccurate claims, overlooked code paths, or important caveats, with file/function references.
3. **Failure analysis:** Ranked causes of missed selected-player kills and multi-kills, with confidence and evidence needed.
4. **Recommended architecture:** A practical local pipeline, with which stages are continuous/cheap and which are conditional/expensive; explain uncertainty handling and how candidates are surfaced.
5. **Accuracy-cost priorities:** A table with expected accuracy benefit, CPU/memory cost, implementation complexity, false-positive risk, and validation method.
6. **Evaluation plan:** Annotation format, metrics (including per-stage recall, attacker/victim attribution precision/recall, event timing tolerance, candidate recall@K, boundary quality, false candidates per hour, and runtime), held-out design, and proposed acceptance thresholds. State which thresholds require product-owner agreement.
7. **Open questions:** Only questions that materially change the recommended architecture.
8. **No implementation:** Finish with a clear proposed order of work, awaiting approval before coding.

Do not infer support for headshots, clutches, wallbangs, weapon identity, or game outcomes from category names, comments, or UI language. Verify those capabilities from executable code.
