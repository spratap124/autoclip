"""Local HUD recognizer behavior and duplicate suppression."""

from __future__ import annotations

from autoclip.pipeline.cs2.models import RegionOfInterest
from autoclip.pipeline.cs2.recognition import (
    KillFeedTracker,
    OCRLine,
    OCRWord,
    RoundTransitionTracker,
    crop_region,
    is_kill_feed_line,
    normalize_player_name,
)


def test_kill_feed_row_filter_rejects_incidental_hud_text() -> None:
    assert is_kill_feed_line(OCRLine("ThakurShaab + AmiaQVO hach1mi", 0.8))
    assert not is_kill_feed_line(OCRLine("Max 10.1ms | 0 fps", 0.9))
    assert not is_kill_feed_line(OCRLine("YouTube 0", 0.9))
    assert not is_kill_feed_line(OCRLine("round starts", 0.5))
    assert not is_kill_feed_line(OCRLine("invite fre", 0.8))
    assert not is_kill_feed_line(OCRLine("Defusal Gr", 0.8))


def test_kill_feed_tracker_does_not_infer_attribution_from_row_substring() -> None:
    tracker = KillFeedTracker(local_player_name="ThakurShaab")
    line = OCRLine("ThakurShaab + AmiaQVO hach1mi", 0.82)

    assert tracker.observe(12.5, [line]) == []
    assert tracker.last_diagnostics[0]["decision"] == "pending_first_observation"
    assert tracker.last_diagnostics[0]["row_confirmed"] is False
    detected = tracker.observe(13.0, [OCRLine("Thakur Shaab + AmiaQVO hach1mi", 0.84)])
    assert len(detected) == 1
    assert tracker.last_diagnostics[0]["decision"] == "confirmed_emitted"
    assert tracker.last_diagnostics[0]["attacker_name"] is None
    assert tracker.last_diagnostics[0]["words"] == []
    assert detected[0].start_s == 12.5
    assert detected[0].evidence_type == "kill_feed_change"
    assert detected[0].supporting_metadata["selected_player_role"] == "unknown"
    assert detected[0].supporting_metadata["attribution_confirmed"] is False

    assert tracker.observe(13.5, [line]) == []
    assert tracker.observe(16.0, [line]) == []


def _positioned_row(attacker: str, victim: str) -> OCRLine:
    return OCRLine(
        f"{attacker} {victim}",
        0.9,
        words=(
            OCRWord(attacker, 0.9, 15, 125),
            OCRWord(victim, 0.9, 255, 365),
        ),
        image_width=400,
    )


def test_player_attribution_distinguishes_attacker_victim_and_other_players() -> None:
    tracker = KillFeedTracker(local_player_name=" ThakurShaaab ")
    attacker = _positioned_row("ThakurShaaab", "SneezyMONST4")
    victim = _positioned_row("SneezyMONST4", "ThakurShaaab")
    other = _positioned_row("SneezyMONST4", "RyukZ")

    assert tracker.observe(1.0, [attacker]) == []
    kill = tracker.observe(1.5, [attacker])[0]
    assert kill.supporting_metadata["attacker_name"] == "ThakurShaaab"
    assert kill.supporting_metadata["victim_name"] == "SneezyMONST4"
    assert kill.supporting_metadata["selected_player_role"] == "attacker"
    assert kill.supporting_metadata["attribution_confidence"] == 1.0
    assert kill.supporting_metadata["attribution_confirmed"] is True

    tracker.observe(2.0, [victim])
    death = tracker.observe(2.5, [victim])[0]
    assert death.supporting_metadata["selected_player_role"] == "victim"

    tracker.observe(3.0, [other])
    unrelated = tracker.observe(3.5, [other])[0]
    assert unrelated.supporting_metadata["selected_player_role"] == "unrelated"
    assert unrelated.supporting_metadata["attribution_confidence"] == 0.0


def test_player_name_normalization_and_possible_ocr_confusions_are_conservative() -> None:
    tracker = KillFeedTracker(local_player_name="ThakurShaaab")
    assert normalize_player_name("  THAKUR   Shaaab  ") == "thakur shaaab"

    possible = _positioned_row("Thakur5haaab", "SneezyMONST4")
    tracker.observe(10.0, [possible])
    event = tracker.observe(10.5, [possible])[0]
    assert event.supporting_metadata["selected_player_role"] == "possible_attacker"
    assert event.supporting_metadata["attribution_confirmed"] is False


def test_similar_player_name_is_not_attributed_by_substring() -> None:
    tracker = KillFeedTracker(local_player_name="ThakurShaab")
    row = _positioned_row("NotThakurShaab", "SneezyMONST4")
    tracker.observe(20.0, [row])
    event = tracker.observe(20.5, [row])[0]
    assert event.supporting_metadata["selected_player_role"] == "unrelated"


def test_single_glyph_name_confusion_is_possible_not_confirmed_attribution() -> None:
    tracker = KillFeedTracker(local_player_name="ThakurShaaab")
    row = _positioned_row("SneezyMONST4", "ThakurShasab")
    tracker.observe(30.0, [row])
    event = tracker.observe(30.5, [row])[0]
    assert event.supporting_metadata["selected_player_role"] == "possible_victim"
    assert event.supporting_metadata["attribution_confidence"] == 0.6
    assert event.supporting_metadata["attribution_confirmed"] is False


def test_kill_feed_tracker_ignores_one_frame_rows_and_same_sample_repeats() -> None:
    tracker = KillFeedTracker()
    line = OCRLine("ink549 V_SinneR", 0.9)

    assert tracker.observe(1.0, [line, line]) == []
    assert tracker.observe(1.5, []) == []
    assert tracker.observe(2.0, []) == []


def test_kill_feed_tracker_matches_ocr_variation_and_rejects_out_of_order_frame() -> None:
    tracker = KillFeedTracker()
    assert tracker.observe(10.0, [OCRLine("bechim umm RyukZ", 0.65)]) == []
    detected = tracker.observe(10.5, [OCRLine("hechim ae RyukZ", 0.72)])
    assert len(detected) == 1
    assert tracker.observe(10.25, [OCRLine("hachimi killed RyukZ", 0.9)]) == []
    # OCR variants of the same persistent entry must remain one event.
    assert tracker.observe(11.0, [OCRLine("hechim ae RyukZ", 0.72)]) == []


def test_kill_feed_tracker_keeps_two_nearby_distinct_victims() -> None:
    tracker = KillFeedTracker()
    first = OCRLine("hachimi kills RyukZ", 0.9)
    second = OCRLine("hachimi kills Eagleeye08", 0.9)
    assert tracker.observe(20.0, [first, second]) == []
    detected = tracker.observe(20.5, [first, second])
    assert len(detected) == 2


def test_round_banner_recognition_and_cooldown() -> None:
    tracker = RoundTransitionTracker(cooldown_s=12)
    banner = [OCRLine("TEAM HACHIMI WINS THE ROUND", 0.92)]

    event = tracker.observe(338.0, banner)
    assert event is not None
    assert event.evidence_type == "round_transition"
    assert event.start_s == 338
    assert tracker.observe(339.5, banner) is None
    assert tracker.observe(351.0, banner) is not None
    assert tracker.observe(360.0, [OCRLine("ordinary gameplay", 0.9)]) is None


def test_normalized_roi_crops_multiple_resolutions() -> None:
    import numpy as np

    roi = RegionOfInterest(x=0.7, y=0.05, width=0.29, height=0.2)
    for width, height in ((1920, 1080), (1280, 720), (1024, 768), (2560, 1440)):
        frame = np.zeros((height, width, 3), dtype=np.uint8)
        cropped = crop_region(frame, roi)
        assert cropped.shape[1] == round(width * roi.width)
        assert cropped.shape[0] == round(height * roi.height)
