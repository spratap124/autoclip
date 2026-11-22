"""Targeted local OCR and duplicate suppression for CS2 HUD observations."""

from __future__ import annotations

import csv
import hashlib
import io
import logging
import re
import shutil
import subprocess
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

import cv2
import numpy as np

from .models import EvidenceRecord, RegionOfInterest

log = logging.getLogger(__name__)
DETECTOR_VERSION = "cs2-events-6"
_EXCLUDED_KILL_TOKENS = {
    "max",
    "ms",
    "youtube",
    "fps",
    "ping",
    "round",
    "starts",
    "team",
    "wins",
    "won",
    "invite",
    "defusal",
    "friends",
    "inventory",
    "loadout",
}
_NON_IDENTITY_TOKENS = {"kill", "kills", "killed", "eliminates", "eliminated"}


@dataclass(frozen=True)
class OCRWord:
    text: str
    confidence: float
    x0: int
    x1: int


@dataclass(frozen=True)
class OCRLine:
    text: str
    confidence: float
    words: tuple[OCRWord, ...] = ()
    image_width: int = 0


def read_lines(
    image: np.ndarray,
    *,
    executable: str | None = None,
    psm: int = 11,
    scale: float = 2.5,
) -> list[OCRLine]:
    """OCR one cropped HUD region with local Tesseract; return grouped text lines."""
    executable = executable or shutil.which("tesseract")
    if not executable:
        return []
    crop = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    if psm == 6 and crop.ndim == 3:
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        crop = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    encoded_ok, encoded = cv2.imencode(".png", crop)
    if not encoded_ok:
        return []
    try:
        result = subprocess.run(
            [executable, "stdin", "stdout", "--psm", str(psm), "tsv"],
            input=encoded.tobytes(),
            capture_output=True,
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.debug("Local Tesseract OCR unavailable: %s", exc)
        return []
    if result.returncode != 0:
        return []

    groups: dict[tuple[str, str, str], list[OCRWord]] = {}
    tsv = io.StringIO(result.stdout.decode("utf-8", errors="replace"))
    reader = csv.DictReader(tsv, delimiter="\t")
    for row in reader:
        token = row.get("text", "").strip()
        if not token:
            continue
        try:
            confidence = float(row.get("conf", "-1"))
        except ValueError:
            continue
        key = (row.get("block_num", ""), row.get("par_num", ""), row.get("line_num", ""))
        try:
            x0 = int(row.get("left", "0"))
            width = int(row.get("width", "0"))
        except ValueError:
            x0, width = 0, 0
        groups.setdefault(key, []).append(
            OCRWord(token, max(0.0, confidence) / 100, x0, x0 + width)
        )

    lines: list[OCRLine] = []
    for words in groups.values():
        text = " ".join(word.text for word in words)
        confidence = sum(word.confidence for word in words) / max(1, len(words))
        if text.strip():
            lines.append(
                OCRLine(
                    text=text,
                    confidence=round(min(1.0, confidence), 3),
                    words=tuple(words),
                    image_width=image.shape[1],
                )
            )
    return lines


def crop_region(image: np.ndarray, roi: RegionOfInterest) -> np.ndarray:
    """Crop a normalized ROI, safely adapting to source resolution/aspect ratio."""
    height, width = image.shape[:2]
    x1 = min(width - 1, max(0, round(roi.x * width)))
    y1 = min(height - 1, max(0, round(roi.y * height)))
    x2 = min(width, max(x1 + 1, round((roi.x + roi.width) * width)))
    y2 = min(height, max(y1 + 1, round((roi.y + roi.height) * height)))
    return image[y1:y2, x1:x2]


def is_kill_feed_line(line: OCRLine) -> bool:
    """Reject HUD labels and incidental text; keep plausible two-player rows."""
    tokens = re.findall(r"[a-z0-9_]{2,}", line.text.casefold())
    if len(tokens) < 2 or sum(len(token) for token in tokens) < 8:
        return False
    return not any(token in _EXCLUDED_KILL_TOKENS for token in tokens)


class KillFeedTracker:
    """Confirm new OCR rows across samples and suppress persistent feed entries."""

    def __init__(self, *, local_player_name: str = "", duplicate_window_s: float = 8.0) -> None:
        self.local_player_name = normalize_player_name(local_player_name)
        self.duplicate_window_s = duplicate_window_s
        self.pending: list[dict] = []
        self.active: list[dict] = []
        self._last_observation_s = -1.0
        self.last_diagnostics: list[dict] = []

    def observe(self, timestamp_s: float, lines: list[OCRLine]) -> list[EvidenceRecord]:
        diagnostics = [
            {
                "ocr_text": line.text,
                "ocr_confidence": line.confidence,
                "image_width": line.image_width,
                "words": [
                    {"text": word.text, "confidence": word.confidence,
                     "x0": word.x0, "x1": word.x1}
                    for word in line.words
                ],
                "kill_feed_line": is_kill_feed_line(line),
                "decision": "rejected_by_line_filter",
                "timestamp_s": timestamp_s,
            }
            for line in lines
        ]
        self.last_diagnostics = diagnostics
        # Reject accidental repeated/out-of-order frames. They otherwise look like
        # independent confirmation and can create duplicate events.
        if timestamp_s <= self._last_observation_s:
            for item in diagnostics:
                item["decision"] = "rejected_out_of_order_sample"
            return []
        self._last_observation_s = timestamp_s
        current = [
            (line, diagnostics[index])
            for index, line in enumerate(lines)
            if diagnostics[index]["kill_feed_line"]
        ]
        emitted: list[EvidenceRecord] = []
        matched_pending: set[int] = set()
        matched_active: set[int] = set()

        for line, diagnostic in current:
            normalized = normalize_text(line.text)
            if not normalized:
                diagnostic["decision"] = "rejected_empty_normalized_row"
                continue
            active_match = self._find_match(normalized, self.active, timestamp_s)
            if active_match is not None:
                matched_active.add(active_match)
                self.active[active_match]["last_seen_s"] = timestamp_s
                diagnostic.update({"decision": "duplicate_active_row", "duplicate_of": active_match,
                                   "row_confirmed": True})
                continue

            pending_match = self._find_match(normalized, self.pending, timestamp_s, max_age_s=1.5)
            if pending_match is None:
                self.pending.append(
                    {
                        "text": line.text,
                        "normalized": normalized,
                        "first_seen_s": timestamp_s,
                        "last_seen_s": timestamp_s,
                        "confidence": line.confidence,
                        "line": line,
                        "observations": 1,
                    }
                )
                diagnostic.update({"decision": "pending_first_observation", "row_confirmed": False,
                                   "pending_age_limit_s": 1.5})
                continue

            item = self.pending[pending_match]
            if timestamp_s <= item["last_seen_s"]:
                diagnostic.update({"decision": "duplicate_same_sample", "row_confirmed": False})
                continue
            matched_pending.add(pending_match)
            item["last_seen_s"] = timestamp_s
            item["confidence"] = max(item["confidence"], line.confidence)
            item["line"] = line
            item["observations"] += 1
            if item["observations"] < 2:
                diagnostic.update({"decision": "pending_confirmation", "row_confirmed": False,
                                   "observations": item["observations"]})
                continue

            normalized = item["normalized"]
            digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:8]
            attacker, victim, split_confidence = _split_kill_feed_row(
                item["line"], self.local_player_name
            )
            role, attribution_confidence = _selected_player_role(
                attacker, victim, self.local_player_name
            )
            confidence = round(min(0.98, 0.35 + 0.55 * item["confidence"] + 0.05), 3)
            emitted.append(
                EvidenceRecord(
                    event_id=f"kill_{round(item['first_seen_s'] * 1000):09d}_{digest}",
                    start_s=item["first_seen_s"],
                    end_s=item["first_seen_s"],
                    evidence_type="kill_feed_change",
                    detection_confidence=confidence,
                    supporting_metadata={
                        "ocr_text": item["text"],
                        "ocr_confidence": item["confidence"],
                        "observations": item["observations"],
                        "attacker_name": attacker,
                        "victim_name": victim,
                        "weapon": None,
                        "is_headshot": None,
                        "selected_player_role": role,
                        "attribution_confidence": attribution_confidence,
                        "attribution_confirmed": role in {"attacker", "victim"},
                        "row_split_confidence": split_confidence,
                    },
                    explanation=(_kill_row_explanation(role, attacker, victim)),
                    detector_version=DETECTOR_VERSION,
                )
            )
            diagnostic.update({
                "decision": "confirmed_emitted",
                "row_confirmed": True,
                "event_id": f"kill_{round(item['first_seen_s'] * 1000):09d}_{digest}",
                "first_seen_s": item["first_seen_s"],
                "observations": item["observations"],
                "attacker_name": attacker,
                "victim_name": victim,
                "attacker_name_match": (
                    _name_match(attacker, self.local_player_name)
                    if attacker and self.local_player_name
                    else None
                ),
                "victim_name_match": (
                    _name_match(victim, self.local_player_name)
                    if victim and self.local_player_name
                    else None
                ),
                "row_split_confidence": split_confidence,
                "selected_player_role": role,
                "attribution_confidence": attribution_confidence,
            })
            self.active.append({"normalized": normalized, "last_seen_s": timestamp_s})

        self.pending = [
            item
            for index, item in enumerate(self.pending)
            if index in matched_pending or timestamp_s - item["first_seen_s"] <= 1.5
        ]
        self.active = [
            item
            for item in self.active
            if timestamp_s - item["last_seen_s"] <= self.duplicate_window_s
        ]
        return emitted

    @staticmethod
    def _find_match(
        normalized: str, items: list[dict], timestamp_s: float, *, max_age_s: float | None = None
    ) -> int | None:
        best_index, best_score = None, 0.0
        for index, item in enumerate(items):
            age = timestamp_s - item.get("last_seen_s", timestamp_s)
            if age < 0:
                continue
            if max_age_s is not None and age > max_age_s:
                continue
            score = _row_similarity(normalized, item["normalized"])
            if score > best_score:
                best_index, best_score = index, score
        return best_index if best_score >= 0.66 else None


class RoundTransitionTracker:
    """Recognize winner banners and suppress repeated OCR of the same banner."""

    def __init__(self, *, cooldown_s: float = 12.0) -> None:
        self.cooldown_s = cooldown_s
        self.last_event_s = -cooldown_s
        self.last_text = ""

    def observe(self, timestamp_s: float, lines: list[OCRLine]) -> EvidenceRecord | None:
        text = " ".join(line.text for line in lines)
        normalized = normalize_text(text)
        has_round = "round" in normalized
        has_result = any(token in normalized for token in ("win", "wins", "won", "victory"))
        if not has_round or not has_result:
            self.last_text = ""
            return None
        if timestamp_s - self.last_event_s < self.cooldown_s:
            self.last_text = normalized
            return None
        self.last_event_s = timestamp_s
        self.last_text = normalized
        best_line_confidence = max((line.confidence for line in lines), default=0)
        confidence = round(min(0.98, 0.55 + 0.35 * best_line_confidence), 3)
        return EvidenceRecord(
            event_id=f"round_{round(timestamp_s * 1000):09d}",
            start_s=timestamp_s,
            end_s=timestamp_s,
            evidence_type="round_transition",
            detection_confidence=confidence,
            supporting_metadata={"ocr_text": text, "banner_match": "round_winner"},
            explanation=(
                "A round-result banner was detected in the configured HUD region; this does "
                "not establish a clutch, ace, or the reason the round ended."
            ),
            detector_version=DETECTOR_VERSION,
        )


def normalize_text(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9_]+", text.casefold()))


def normalize_player_name(text: str) -> str:
    """Normalize Unicode presentation and whitespace without fuzzy identity matching."""
    normalized = unicodedata.normalize("NFKC", text)
    normalized = "".join(char for char in normalized if unicodedata.category(char) != "Cf")
    return " ".join(normalized.casefold().split())


def _split_kill_feed_row(
    line: OCRLine, selected_name: str = ""
) -> tuple[str | None, str | None, float]:
    """Use the row's horizontal layout to separate attacker and victim text.

    CS2 places the killer left of the weapon icon and the victim right of it.
    The icon itself can be misread as words, so the row's visual midpoint is a
    more stable separator than a single OCR whitespace gap. A selected name is
    also sought as a short contiguous word sequence on either side.
    """
    if len(line.words) < 2 or line.image_width <= 0:
        return None, None, 0.0
    words = sorted(line.words, key=lambda word: word.x0)
    row_left = min(word.x0 for word in words)
    row_right = max(word.x1 for word in words)
    row_width = row_right - row_left
    if row_width < line.image_width * 0.08:
        return None, None, 0.0
    midpoint = row_left + row_width / 2
    left = [word for word in words if (word.x0 + word.x1) / 2 < midpoint]
    right = [word for word in words if (word.x0 + word.x1) / 2 >= midpoint]
    if not left or not right:
        return None, None, 0.0

    left_match = _find_name_sequence(left, selected_name) if selected_name else None
    right_match = _find_name_sequence(right, selected_name) if selected_name else None
    if left_match and right_match:
        return None, None, 0.0
    if left_match:
        attacker = left_match[0]
        victim = _join_name(right)
    elif right_match:
        attacker = _join_name(left)
        victim = right_match[0]
    else:
        attacker = _join_name(left)
        victim = _join_name(right)

    if len(normalize_player_name(attacker)) < 2 or len(normalize_player_name(victim)) < 2:
        return None, None, 0.0
    return attacker, victim, 0.75


def _join_name(words: tuple[OCRWord, ...] | list[OCRWord]) -> str:
    return " ".join(word.text.strip() for word in words if word.text.strip())


def _find_name_sequence(words: list[OCRWord], selected_name: str) -> tuple[str, str] | None:
    matches: list[tuple[int, str, str]] = []
    for start in range(len(words)):
        for end in range(start + 1, min(len(words), start + 4) + 1):
            observed = _join_name(words[start:end])
            match = _name_match(observed, selected_name)
            if match:
                matches.append((0 if match == "exact" else 1, observed, match))
    if not matches:
        return None
    _, observed, match = min(matches, key=lambda item: (item[0], len(item[1])))
    return observed, match


def _selected_player_role(
    attacker: str | None, victim: str | None, selected_name: str
) -> tuple[str, float]:
    if not selected_name:
        return "unselected", 0.0
    if not attacker or not victim:
        return "unknown", 0.0

    attacker_match = _name_match(attacker, selected_name)
    victim_match = _name_match(victim, selected_name)
    if attacker_match == "exact" and victim_match == "exact":
        return "unknown", 0.0
    if attacker_match == "exact":
        return "attacker", 1.0
    if victim_match == "exact":
        return "victim", 1.0
    if attacker_match == "possible" and victim_match == "possible":
        return "unknown", 0.0
    if attacker_match == "possible":
        return "possible_attacker", 0.6
    if victim_match == "possible":
        return "possible_victim", 0.6
    return "unrelated", 0.0


def _name_match(observed: str, selected: str) -> str | None:
    observed_normalized = normalize_player_name(observed)
    selected_normalized = normalize_player_name(selected)
    if observed_normalized == selected_normalized:
        return "exact"

    # Tesseract may split one username into adjacent words. Ignore whitespace
    # for that case while retaining punctuation and all visible name characters.
    if observed_normalized.replace(" ", "") == selected_normalized.replace(" ", ""):
        return "exact"

    observed_compact = re.sub(r"[^\w]", "", observed_normalized)
    selected_compact = re.sub(r"[^\w]", "", selected_normalized)
    if not observed_compact or len(observed_compact) != len(selected_compact):
        return None
    if observed_compact == selected_compact:
        return "possible"

    # Single-character OCR confusions may be surfaced as possible attribution,
    # but never promoted to a confirmed selected-player event.
    ocr_map = str.maketrans({"0": "o", "1": "l", "|": "l", "5": "s", "$": "s"})
    if observed_compact.translate(ocr_map) == selected_compact.translate(ocr_map):
        return "possible"
    if min(len(observed_compact), len(selected_compact)) >= 8 and _edit_distance_at_most_one(
        observed_compact, selected_compact
    ):
        return "possible"
    return None


def _edit_distance_at_most_one(first: str, second: str) -> bool:
    if abs(len(first) - len(second)) > 1:
        return False
    previous = current = 0
    edits = 0
    while previous < len(first) and current < len(second):
        if first[previous] == second[current]:
            previous += 1
            current += 1
            continue
        edits += 1
        if edits > 1:
            return False
        if len(first) > len(second):
            previous += 1
        elif len(second) > len(first):
            current += 1
        else:
            previous += 1
            current += 1
    edits += previous < len(first) or current < len(second)
    return edits <= 1


def _kill_row_explanation(role: str, attacker: str | None, victim: str | None) -> str:
    if role == "attacker":
        return f"OCR row attributes {attacker!r} as attacker against {victim!r}."
    if role == "victim":
        return f"OCR row attributes {victim!r} as victim of {attacker!r}."
    if role.startswith("possible_"):
        return (
            "OCR resembles the selected player on one side of the row, "
            "but attribution is unconfirmed."
        )
    if role == "unrelated":
        return "The parsed row did not establish the selected player as attacker or victim."
    return "A kill-feed-like OCR row was observed, but attacker/victim attribution is unknown."


def _row_similarity(first: str, second: str) -> float:
    """Compare OCR rows robustly when Tesseract changes a few player-name glyphs."""
    first_tokens = [token for token in first.split() if token not in _NON_IDENTITY_TOKENS]
    second_tokens = [token for token in second.split() if token not in _NON_IDENTITY_TOKENS]
    if not first_tokens or not second_tokens:
        return 0.0
    # Longest common fuzzy subsequence tolerates OCR substitutions while keeping
    # attacker/victim order. An unordered set made a reciprocal kill look like
    # the persistent earlier row and hid the second event.
    rows = len(first_tokens) + 1
    cols = len(second_tokens) + 1
    matches = [[0] * cols for _ in range(rows)]
    for i, first_token in enumerate(first_tokens, start=1):
        for j, second_token in enumerate(second_tokens, start=1):
            ratio = SequenceMatcher(None, first_token, second_token).ratio()
            token_match = first_token == second_token or (
                min(len(first_token), len(second_token)) >= 4 and ratio >= 0.72
            )
            matches[i][j] = (
                matches[i - 1][j - 1] + 1
                if token_match
                else max(matches[i - 1][j], matches[i][j - 1])
            )
    overlap = matches[-1][-1]
    dice = 2 * overlap / (len(first_tokens) + len(second_tokens))
    sequence = SequenceMatcher(None, first, second).ratio()
    # Two stable tokens (often the attacker/victim) are useful even if a third
    # token, such as an assist name, is recognized differently on the next frame.
    if overlap >= 2:
        return max(sequence, dice)
    # A single shared attacker is not enough: that player can make several
    # nearby kills. With fewer than two player-like tokens, do not merge rows.
    return min(sequence, 0.6)
