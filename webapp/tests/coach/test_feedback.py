import pytest

from app.services import coach


def test_voice_notes_are_kept_in_order_and_can_be_switched_off(db):
    first = coach.add_note(db, "voice", "  Stop peeking that dry.  ")
    coach.add_note(db, "voice", "Good trade, do that every round.")
    assert coach.active_notes(db, "voice") == ["Stop peeking that dry.", "Good trade, do that every round."]
    assert coach.set_note_active(db, first.id, False) is True
    assert coach.active_notes(db, "voice") == ["Good trade, do that every round."]
    assert coach.set_note_active(db, 9999, False) is False


def test_an_empty_note_is_refused(db):
    with pytest.raises(coach.ReportError, match="body"):
        coach.add_note(db, "voice", "   ")
