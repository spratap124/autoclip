# CS2 Missed-Kill Diagnostics

Diagnostics are optional, local, and disabled by default. They observe the current CS2 pipeline; they do not change OCR settings, matching rules, candidate thresholds, or exports. Diagnostic mode reruns OCR over cached sampled frames when available, so it adds OCR and disk I/O but does not force a full source decode if the frame cache is valid.

## Enable in the app

1. Expand **Options** on the Ingest page.
2. Choose CS2 workflow and enter the selected in-game player name as usual.
3. Check **Save local CS2 diagnostics**.
4. Run the job normally. Existing clip output and export behavior are unchanged.

Files are written under `~/.autoclip/work/<job-id>/cs2/diagnostics/` (or `$AUTOCLIP_HOME/work/<job-id>/cs2/diagnostics/`):

- `frames.jsonl`: one record per sampled frame, including gate outcomes, OCR decision, OCR lines/words, position/confidence, parsed names, attribution, and row-tracker decision.
- `candidate_decisions.jsonl`: emitted kill records and whether each passed the selected-player candidate gate and remained in a final candidate.
- `kill_feed_crops/`: representative kill-feed crops. The default is at most 100 crops and 16 MiB total per diagnostic run. Set `diagnostics_max_crops` to 0 to disable crop writing while retaining JSONL.
- `summary.json`: frame and crop counts/caps.

The per-job evidence endpoint and review page continue to work as before. Diagnostic files are local work artifacts and are not uploaded or sent to a service. A later diagnostic run replaces only this diagnostic folder’s owned frame log and `killfeed_*.jpg` crops.

## Example frame record

```json
{
  "timestamp_s": 385.5,
  "frame_decoded": true,
  "kill_feed_roi": {"name": "kill_feed", "x": 0.7, "y": 0.035, "width": 0.295, "height": 0.22},
  "kill_feed_rows_gate": true,
  "kill_feed_roi_change": 11.42,
  "kill_feed_change_gate": true,
  "pending_confirmation_before_frame": true,
  "kill_feed_ocr_invoked": true,
  "kill_feed_rows": [
    {
      "ocr_text": "ThakurShaaab + V_SinneR ... hachimi",
      "ocr_confidence": 0.56,
      "words": [{"text": "ThakurShaaab", "confidence": 0.72, "x0": 15, "x1": 210}],
      "decision": "confirmed_emitted",
      "row_confirmed": true,
      "attacker_name": "ThakurShaaab",
      "victim_name": "... hachimi",
      "attacker_name_match": "exact",
      "victim_name_match": null,
      "selected_player_role": "attacker",
      "attribution_confidence": 1.0,
      "event_id": "kill_000385000_abcdef12"
    }
  ],
  "round_ocr_eligible_time": true,
  "round_banner_color_gate": false,
  "kill_feed_crop": "kill_feed_crops/killfeed_000771_385.500.jpg"
}
```

The example uses illustrative values. OCR word coordinates are in the scaled OCR crop coordinate system; the stored crop is the original sampled ROI. Row decisions include line-filter rejection, first pending observation, pending confirmation, same-sample repeat, persistent duplicate, confirmed event, and out-of-order rejection.

## Evaluate a local recording with supplied labels

The standalone command takes an explicitly selected local video and a JSON file of user-supplied labels. It never fetches media. It samples the full selected recording, so only run it when you intend that analysis. It uses the current sampling and recognition algorithm as-is and writes all results to the specified output directory.

Create `labels.json` by manually reviewing the recording. Do not add an event unless you have verified it; `note` can record where the label came from.

```json
{
  "local_player_name": "thakurshaaab",
  "tolerance_s": 2.0,
  "max_crops": 100,
  "known_kills": [
    {"timestamp_s": 385.0, "expected_role": "attacker", "note": "manual review at 385 s"},
    {"timestamp_s": 410.5, "expected_role": "attacker", "note": "manual review at 410.5 s"}
  ],
  "expected_multi_kill_sequences": [
    {"label": "manual triple sequence", "expected_count": 3, "kill_timestamps_s": [385.0, 386.0, 387.0]}
  ]
}
```

Run from the repository root, using the project environment:

```bash
PYTHONPATH=backend .venv/bin/python -m autoclip.pipeline.cs2.evaluate \
  /local/path/to/recording.mp4 \
  --labels /local/path/to/labels.json \
  --output /local/path/to/cs2-diagnostic-output
```

The output contains `evaluation.json` and `samples/diagnostics/`. For each supplied kill timestamp, `loss_stage` distinguishes no nearby sample, pixel gate/sampling, OCR/line filtering, confirmation/parsing, event persistence/matching, candidate filtering/grouping, or a candidate that includes the event. Read the nearby frame and row records before interpreting this summary. `expected_multi_kill_sequences` reports stage results for the exact user-supplied timestamps and whether their events share a candidate rank; it does not create or validate labels. No precision/recall is calculated and `accuracy_claim` remains null.

The annotation file is deliberately external to application settings and is not written into the app database. Only its selected player name and manual labels enter the evaluation output; the absolute recording and label-file paths are omitted.

## How to identify the stage that lost a kill

For a verified timestamp, inspect records within the configured tolerance:

1. No sample near it: sampling/timebase or incorrect label timestamp.
2. `kill_feed_rows_gate=false`: dark-band gate blocked OCR. If true but change gate false with no pending row, the ROI-difference gate blocked OCR.
3. OCR invoked, but no plausible row: OCR recognition or line filter.
4. Row present but pending / duplicate / rejected: confirmation, similarity, or tracker behavior.
5. Confirmed row has wrong names or role: word geometry split, OCR tokenization, or selected-name matching.
6. Event has role other than `attacker`, or attribution below 0.9: it cannot anchor a selected-player candidate under existing rules.
7. Anchor passed but no final candidate: inspect candidate decision and neighboring round/group events for overlap deduplication or the candidate cap.
8. Candidate exists but not surfaced as expected: inspect rank and the Review list; compare its interval to the labeled sequence.

## Initial investigation order

No new recording has been evaluated by this feature yet, so the following order reflects diagnostic value rather than measured frequency:

1. Inspect verified events classified at pixel gates or with no OCR sample; save representative crops before considering ROI changes.
2. Inspect rows that OCR saw but did not confirm or parse; compare word positions and the stored crop to the actual kill-feed layout.
3. Inspect confirmed events with possible/unknown/unrelated roles; quantify attacker/victim confusion before changing identity rules.
4. Inspect anchors removed after grouping/dedupe/candidate cap.
5. Only after a manually verified set exists, rank changes by recovered events per added CPU second and false candidate rate.
