import pytest

from dunzo import content_repo, progress_repo


@pytest.fixture
def note(conn):
    deck = content_repo.create_deck(conn, "d")
    player = content_repo.create_player(conn, "p", "x")
    return content_repo.create_note(conn, deck.id, player.id, 3, {})


def record(conn, note_id, index, at, grade=3, detail=None, before=None):
    progress_repo.record_review(
        conn, note_id=note_id, card_index=index, reviewed_at=at, grade=grade, detail=detail,
        scheduler="sm2", state_before=before, state_after={"interval": 1}, due=at + 1,
    )


def test_no_reviews(conn, note):
    assert progress_repo.list_reviews(conn, note.id) == []


def test_newest_first_with_parsed_fields(conn, note):
    record(conn, note.id, 0, 10.0, grade=3, detail={"typed": "a"})
    record(conn, note.id, 0, 20.0, grade=1, before={"interval": 1})
    first, second = progress_repo.list_reviews(conn, note.id)[::-1]
    assert (first.reviewed_at, first.grade, first.detail, first.state_before) == (10.0, 3, {"typed": "a"}, None)
    assert second.state_before == {"interval": 1} and second.detail is None
    assert second.state_after == {"interval": 1}
    assert [e.reviewed_at for e in progress_repo.list_reviews(conn, note.id)] == [20.0, 10.0]


def test_same_time_is_ordered_by_insertion(conn, note):
    record(conn, note.id, 0, 5.0, grade=1)
    record(conn, note.id, 0, 5.0, grade=2)
    assert [e.grade for e in progress_repo.list_reviews(conn, note.id)] == [2, 1]


def test_filter_by_card_and_limit(conn, note):
    for i in range(5):
        record(conn, note.id, i % 2, float(i))
    assert {e.card_index for e in progress_repo.list_reviews(conn, note.id, card_index=1)} == {1}
    assert len(progress_repo.list_reviews(conn, note.id, limit=2)) == 2


def test_other_notes_are_not_mixed_in(conn, note):
    other = content_repo.create_note(
        conn, note.deck_id, note.player_id, 1, {}
    )
    record(conn, other.id, 0, 1.0)
    assert progress_repo.list_reviews(conn, note.id) == []
