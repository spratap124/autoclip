# CS2 Demo Event Schema

Status: upstream API reviewed and one locally available compressed real demo
was parsed with the pinned parser and current schema v3 adapter. Its match
identity and player names are intentionally omitted. Observations apply only
to that recording and are not a general accuracy claim.

## Parser and format

- Dependency pin: `demoparser2==0.42.2` (PyPI latest observed on
  2026-11-22; a compatible CPython 3.11/3.12 macOS arm64 wheel is published).
- The project environment initially lacked the package; `demoparser2==0.42.2`
  was installed and pinned in `pyproject.toml`.
- Upstream describes this as a CS2 replay/demo parser. `DemoParser(path)` opens
  a file; it does not document an upload-signature validator. AutoClip validates
  the Source 2 signature independently and lets the parser validate structure.
- `test-data/local/match.dem` is an untracked Zstandard-compressed file (about
  241 MiB compressed, 329 MiB expanded). Its decompressed prefix is
  `PBDEMS2\0`. It was not modified; parser probing used a temporary expanded
  copy that was deleted automatically. Do not stage or commit `test-data/`.
- This local demo was also inspected while adjusting round-boundary handling;
  it is development/tuning evidence, not a held-out validation recording.

## Verified Python binding API

The upstream README and Python binding source expose:

```python
from demoparser2 import DemoParser

parser = DemoParser("match.dem")
header = parser.parse_header()
players = parser.parse_player_info()
event_names = parser.list_game_events()
deaths = parser.parse_event("player_death")
ticks = parser.parse_ticks(["is_alive", "team_num"], ticks=[1000, 2000])
```

These return a header mapping, pandas-compatible tables for player/event/tick
queries, and a list of event names. The adapter exercised `parse_events(names)`
and iterated its `(event_name, frame)` pairs. `parse_event(name, *, player=None,
other=None)` and `parse_events(names, *, player=None, other=None)` allow callers
to request event player properties and other properties; requested property
names are translated by the binding. `parse_ticks(props, *, players=None,
ticks=None, prop_states=None)` supports property queries at requested ticks.
Errors from Rust are surfaced as Python exceptions. This API shape was verified
from upstream source and exercised against the installed package.

## Verified fields and types

| Source | Field | Type / nullability | Meaning |
|---|---|---|---|
| `parse_header()` | `map_name` | string (header mapping) | Map name |
| `parse_header()` | `network_protocol` | string (header mapping) | Network protocol identifier |
| `parse_header()` | `demo_version_name`, `demo_version_guid` | string (header mapping) | Demo version metadata |
| `parse_player_info()` | `steamid` | nullable unsigned 64-bit | Steam identity when present |
| `parse_player_info()` | `name` | nullable string | Player display name |
| `parse_player_info()` | `team_number` | nullable signed 32-bit | Parser-reported team number |
| `parse_ticks()` | `is_alive` | documented player property; runtime dtype demo-dependent | Pawn alive state |
| `parse_ticks()` | `team_num` | documented player property; runtime dtype demo-dependent | Player team number |
| `parse_ticks()` | `game_time` | documented alias for `net_tick` | Tick index, not seconds |
| `parse_ticks()` | `player_name` | documented player property | Name as represented in player state |
| `parse_ticks()` | `player_steamid` | documented player property | Steam identity in player state |
| `parse_ticks()` | `is_warmup_period` | documented game-state property | Warmup state |
| `parse_ticks()` | `total_rounds_played` | documented game-state property | Round counter/state field |
| `parse_ticks()` | `round_in_progress` | documented game-state property | Round active state |
| `parse_ticks()` | `round_win_reason` | documented game-state property | Round outcome reason enum/value |
| `parse_ticks()` | `is_bomb_planted` | documented game-state property | Bomb state |
| `parse_ticks()` | `team_rounds_total` | observed integer per team | Counter used for conservative round outcome inference |

### Observed event rows in the available recording

The below are exact observed event DataFrame columns and dtypes from this single
local sample. Nullable string-like event fields surfaced by pandas as `str` in
this sample; this does not prove that another demo cannot contain nulls.

| Event | Rows | Observed columns (dtype) |
|---|---:|---|
| `player_death` | 168 | `tick` (int32), `attacker_steamid` (str), `attacker_name` (str), `user_steamid` (str), `user_name` (str), `assister_steamid` (str), `assister_name` (str), `weapon` (str), `headshot` (bool), `penetrated` (int32), `thrusmoke` (bool), `noscope` (bool), `attackerblind` (bool), `attackerinair` (bool), `assistedflash` (bool), `distance` (float32), plus other event fields |
| `round_freeze_end` | 25 | `tick` (int32) |
| `round_officially_ended` | 44 | `tick` (int32) |
| `bomb_planted` | 11 | `tick` (int32), `user_steamid` (str), `user_name` (str), `c4` (int32), `site` (int32) |
| `bomb_defused` | 4 | same columns as `bomb_planted` |
| `bomb_exploded` | 4 | same columns as `bomb_planted` |
| `player_spawn` | 250 | `tick` (int32), `user_steamid` (str), `user_name` (str) |
| `player_disconnect` | 4 | `tick` (int32), user/name/network fields, `reason` (int32), `ever_fully_connected` (bool), `xuid` (uint64) |

The same demo was queried at 504 distinct ticks around its kill events with
`parse_ticks(["is_alive", "team_num", "team_rounds_total", ...],
players=<SteamID64s>, ticks=<ticks>)`. Observed columns included `is_alive`
(bool), `team_num` (uint32), `team_rounds_total` (integer), `tick` (int32),
`steamid` (uint64), and `name` (string). It returned 5,040 player-state rows.
For all 168 death rows, the victim was alive at event tick minus one and dead at
the event tick and tick plus one. This is a one-recording consistency
observation, not independent ground-truth validation. It supports querying
survivors around event ticks, while event ordering and simultaneous deaths
still require conservative rules and more recordings.

This recording's `list_game_events()` includes `round_freeze_end`,
`round_officially_ended`, `round_prestart`, `round_poststart`, and `cs_pre_restart`,
but not `round_start` or `round_end`. Therefore the detector cannot depend on
one canonical round event. `round_officially_ended` is observed more often than
the 25 freeze-end events; matching boundaries uses the next freeze-end as a
fallback, not a one-to-one assumption. Current reconstruction uses
freeze-end as the active lower bound and an observed round-end event or the
next freeze-end to close a round. A winner is inferred only when exactly one
team score increases by one and team-number assignments remain stable.

Upstream binding constructs `parse_player_info()` columns named exactly
`steamid`, `name`, `team_number`; its implementation types SteamID as
`Option<u64>`, team as `Option<i32>`, and name as `Option<String>`. This makes
missing IDs/names possible and requires session-local identity handling.

## Event inventory and schema boundaries

`list_game_events()` returns event names actually present in a given demo.
The API exposes `parse_event` / `parse_events`, but event field columns and
nullability depend on the event schema and recording. The upstream README does
not publish a stable complete event field/type table. The one local sample
verifies the `player_death`, round, and bomb payloads listed here only for that
recording; the adapter inspects the event inventory and records missing field
warnings rather than assuming every optional stream exists.

The sample directly verifies the event names and columns listed above only for
that recording. `round_start` and `round_end` were absent in it. At runtime the
parser adapter must first query `list_game_events()`, request only events
present, inspect returned columns, normalize known fields, and record
missing/unrecognized fields as warnings. It must not assume every event family
is present in every demo.

The sample contains typed headshot, penetration, through-smoke, no-scope,
attacker-blind, weapon, assister, and attacker/victim identity columns. These
fields are available in this sample; availability and completeness across
recording sources remain unverified. `penetrated` is an integer count in the
sample, so a wallbang predicate must use positive values, not generic truthiness
on potentially missing values.

## Tick and time semantics

Upstream documents `game_time` as `net_tick`; `parse_ticks` accepts integer
tick values. This confirms tick-index queries, not a fixed tick rate or direct
conversion to video time. The exact tick-rate source/metadata and seconds
conversion for a given demo remain to be verified from a real file or more
specific parser documentation. Preserve ticks as canonical timestamps; derived
seconds must be marked estimated if tick-rate evidence is missing.

Round state can be queried through properties including warmup state, rounds
played, round-in-progress, round-win reason, and team score. Current code uses
the score only when team assignments stay stable and exactly one team score
advances. Transition timing, restarts, overtime, pauses, and state completeness
still need independent labeled-demo validation.

## Feasibility assessment

| Requirement | Assessment from verified API | Required conservative behavior |
|---|---|---|
| Player-specific kills | Implemented from event SteamIDs and sampled teams; one demo yielded 168/168 deaths assigned to 25 round windows | Keep IDs unknown when event identity/team evidence is absent; sample is not ground truth |
| Multi-kills | Implemented from distinct selected-player enemy victims within one inferred round | Round-based grouping may merge separate sequences; manually labeled timing evaluation is still needed |
| Round segmentation | Implemented using freeze-end and observed end events, with next-start fallback | Preserve incomplete boundaries and warn; no accuracy claim from the sample |
| Clutches | Implemented from sparse event-tick alive/team states and score outcome when available | Strict state completeness by default; incomplete attempts remain unknown or omitted |
| Accurate demo timestamps | Tick timestamps are feasible; seconds require verified tick-rate metadata | Keep ticks canonical and never equate demo time with video time |
| Specialized kill tags | Headshot, penetration, smoke, no-scope, blind, weapon, assists verified in the sample | Emit only when the parser supplied an affirmative field; availability across demos remains unmeasured |

## Environment and validation status

- Parser header/event/player/tick API was verified against upstream source and
  exercised on the available local compressed demo.
- The current schema v3 adapter ran in an isolated child process with a
  4,096 MiB address-space limit and a 300-second timeout. Decompressing the
  available 241 MiB Zstandard fixture into a temporary 329 MiB demo and parsing
  it took 2.35 seconds total on this machine. The parser returned 10 roster
  players; reconstruction created 25 round windows and assigned all 168
  `player_death` rows to those windows. This is internal consistency evidence,
  not independent event validation or an accuracy score.
- On a separate run with the decompressed demo already present, `parse_cached`
  (SHA-256, isolated parsing, normalization, and cache write) took 1.638
  seconds. The normalized JSON cache was 1,669,612 bytes. Ten reloads measured
  a 0.0091 second median; ten selected-player analysis runs over that real cached
  match measured an 0.0114 second median. These local measurements exclude the
  HTTP server and browser and are not generalized performance guarantees.
- A synthetic 10-player, 25-round, 168-death-row analysis measured a 0.0049
  second median over ten pure player-analysis runs. This is not a real cached
  match/API benchmark and excludes upload, database, and browser latency.
- Peak memory and normalized cache size were not measured. The parser's
  configured address-space limit is not a measured peak-memory result.
- Header, player-info, event, round, and tick schemas were observed. No `.dem`
  file was downloaded; the available local recording was used during
  development to select the freeze-end boundary behavior.
- No manually labeled kills or clutches were available, so precision/recall
  and real-demo detector accuracy remain unmeasured.
- Manually verified selected-player kills: 0. True positives, false positives,
  false negatives, precision, and recall: not measurable. The same one local
  recording informed development and there is no separate held-out demo.
- Multi-kills, aces, and clutches were not independently compared against
  manually labeled outcomes.
- A manually labeled recording is still needed to validate kill recall,
  round assignment, and clutch classification. This one sample is insufficient
  for general event-completeness or supported-format claims.

## Sources

- [demoparser2 on PyPI](https://pypi.org/project/demoparser2/)
- [demoparser upstream README](https://github.com/LaihoE/demoparser/blob/main/README.md)
- [demoparser upstream Python binding](https://github.com/LaihoE/demoparser/blob/main/src/python/src/lib.rs)
