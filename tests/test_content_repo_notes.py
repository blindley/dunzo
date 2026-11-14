import io
import json

import pytest

from dunzo import content_repo as repo

SCHEMA = json.dumps(
    {
        "type": "object",
        "properties": {"front": {"type": "string", "maxLength": 10}, "n": {"type": "integer"}},
        "required": ["front"],
    }
)


@pytest.fixture
def deck(conn):
    return repo.create_deck(conn, "deck")


@pytest.fixture
def player(conn):
    return repo.create_player(conn, "p", "x")


def add(conn, deck, player, text="a", **kw):
    return repo.create_note(conn, deck.id, player.id, 1, {"t": text}, **kw)


def order(conn, deck):
    return [n.properties["t"] for n in repo.list_notes(conn, deck.id)]


def positions(conn, deck):
    return [n.position for n in repo.list_notes(conn, deck.id)]


# ---------------------------------------------------------------------------- create/get


def test_create_appends_with_gaps(conn, deck, player):
    a, b, c = (add(conn, deck, player, t) for t in "abc")
    assert [a.position, b.position, c.position] == [1024, 2048, 3072]
    assert order(conn, deck) == ["a", "b", "c"]
    assert a.player_name == "p"
    assert a.created_at == a.updated_at
    assert repo.get_note(conn, a.id) == a
    assert repo.get_note(conn, "nope") is None


def test_create_never_touches_progress_tables(conn, deck, player):
    add(conn, deck, player)
    for table in ("card_state", "review_log"):
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_properties_round_trip_unicode(conn, deck, player):
    n = repo.create_note(conn, deck.id, player.id, 2, {"front": "日本語", "tags": ["a", "b"]})
    assert repo.get_note(conn, n.id).properties == {"front": "日本語", "tags": ["a", "b"]}
    assert repo.get_note(conn, n.id).card_count == 2


def test_unknown_deck_or_player(conn, deck, player):
    with pytest.raises(ValueError, match="Deck not found"):
        repo.create_note(conn, "x", player.id, 1, {})
    with pytest.raises(ValueError, match="Player not found"):
        repo.create_note(conn, deck.id, "x", 1, {})


@pytest.mark.parametrize("bad", [0, -1, 1001, "2", 1.5, True, None])
def test_card_count_validated(conn, deck, player, bad):
    with pytest.raises(ValueError, match="between 1 and"):
        repo.create_note(conn, deck.id, player.id, bad, {})
    assert repo.list_notes(conn, deck.id) == []


def test_properties_must_be_an_object(conn, deck, player):
    with pytest.raises(repo.NoteInvalid, match="JSON object"):
        repo.create_note(conn, deck.id, player.id, 1, ["x"])


def test_schema_validation_reports_every_problem(conn, deck):
    p = repo.create_player(conn, "schema", "x", SCHEMA)
    with pytest.raises(repo.NoteInvalid) as info:
        repo.create_note(conn, deck.id, p.id, 1, {"front": "much too long text", "n": "x"})
    assert len(info.value.messages) == 2
    assert any(m.startswith("front:") for m in info.value.messages)
    assert any(m.startswith("n:") for m in info.value.messages)
    with pytest.raises(repo.NoteInvalid, match="required"):
        repo.create_note(conn, deck.id, p.id, 1, {})
    assert repo.list_notes(conn, deck.id) == []
    assert repo.create_note(conn, deck.id, p.id, 1, {"front": "ok", "extra": 1})  # extras allowed


def test_no_schema_accepts_any_object(conn, deck, player):
    assert repo.validate_properties(None, {"anything": [1, {"x": None}]}) == []


def test_unresolvable_ref_is_reported_not_raised(conn, deck):
    schema = json.dumps({"type": "object", "properties": {"a": {"$ref": "other.json#/x"}}})
    p = repo.create_player(conn, "ref", "x", schema)
    with pytest.raises(repo.NoteInvalid, match="could not be applied"):
        repo.create_note(conn, deck.id, p.id, 1, {"a": 1})


# ------------------------------------------------------------------------ update/delete


def test_update_changes_values_and_timestamp(conn, deck, player):
    n = add(conn, deck, player)
    u = repo.update_note(conn, n.id, 3, {"t": "new"})
    assert (u.card_count, u.properties) == (3, {"t": "new"})
    assert u.updated_at >= n.updated_at
    assert (u.position, u.deck_id) == (n.position, n.deck_id)


def test_update_validates_and_leaves_note_unchanged(conn, deck):
    p = repo.create_player(conn, "schema", "x", SCHEMA)
    n = repo.create_note(conn, deck.id, p.id, 1, {"front": "ok"})
    with pytest.raises(repo.NoteInvalid):
        repo.update_note(conn, n.id, 1, {})
    with pytest.raises(ValueError):
        repo.update_note(conn, n.id, 0, {"front": "ok"})
    assert repo.get_note(conn, n.id) == n


def test_update_missing_returns_none(conn):
    assert repo.update_note(conn, "nope", 1, {}) is None


def test_update_leaves_progress_alone_even_when_shrinking(conn, deck, player):
    n = repo.create_note(conn, deck.id, player.id, 4, {})
    conn.execute("INSERT INTO card_state VALUES (?, 3, 1.0, 'sm2', '{}')", (n.id,))
    repo.update_note(conn, n.id, 1, {})
    assert conn.execute("SELECT COUNT(*) FROM card_state").fetchone()[0] == 1


def test_delete_cascades_but_keeps_media(conn, deck, player, tmp_path):
    n = add(conn, deck, player)
    media = repo.add_media(conn, tmp_path / "m", io.BytesIO(b"x"), "image/png")
    repo.link_note_media(conn, n.id, media.id)
    conn.execute("INSERT INTO card_state VALUES (?, 0, 1.0, 'sm2', '{}')", (n.id,))
    assert repo.delete_note(conn, n.id) is True
    assert repo.delete_note(conn, n.id) is False
    for table in ("card_state", "note_media"):
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    assert repo.get_media(conn, media.id) is not None


# ------------------------------------------------------------------------------ ordering


def test_insert_before_uses_midpoints(conn, deck, player):
    a, b = add(conn, deck, player, "a"), add(conn, deck, player, "b")
    x = add(conn, deck, player, "x", before_note_id=b.id)
    assert a.position < x.position < b.position
    assert x.position == (1024 + 2048) // 2
    assert order(conn, deck) == ["a", "x", "b"]


def test_insert_at_front(conn, deck, player):
    a = add(conn, deck, player, "a")
    x = add(conn, deck, player, "x", before_note_id=a.id)
    assert x.position == 512
    assert order(conn, deck) == ["x", "a"]


def test_insert_renumbers_when_gap_runs_out(conn, deck, player):
    add(conn, deck, player, "a")
    b = add(conn, deck, player, "b")
    for _ in range(40):  # always insert right before b: halves the gap each time
        add(conn, deck, player, "n", before_note_id=b.id)
    result = order(conn, deck)
    assert result[0] == "a" and result[-1] == "b" and len(result) == 42
    ps = positions(conn, deck)
    assert ps == sorted(set(ps)) and all(p > 0 for p in ps)


def test_front_insert_renumbers_when_gap_runs_out(conn, deck, player):
    first = add(conn, deck, player, "first")
    for _ in range(12):  # 1024 -> 512 -> ... -> 1: then a renumber is needed
        add(conn, deck, player, "n", before_note_id=first.id)
    assert order(conn, deck)[-1] == "first" and len(order(conn, deck)) == 13
    assert len(set(positions(conn, deck))) == 13


def test_insert_before_note_of_another_deck_fails(conn, deck, player):
    other = repo.create_deck(conn, "other")
    foreign = add(conn, other, player)
    with pytest.raises(ValueError, match="not in this deck"):
        add(conn, deck, player, before_note_id=foreign.id)
    assert repo.list_notes(conn, deck.id) == []


def test_move_within_deck(conn, deck, player):
    a, b, c, d = (add(conn, deck, player, t) for t in "abcd")
    repo.move_note(conn, d.id, before_note_id=b.id)
    assert order(conn, deck) == ["a", "d", "b", "c"]
    repo.move_note(conn, a.id)  # no target: to the end
    assert order(conn, deck) == ["d", "b", "c", "a"]
    repo.move_note(conn, c.id, before_note_id=c.id)  # before itself: nothing happens
    assert order(conn, deck) == ["d", "b", "c", "a"]
    repo.move_note(conn, b.id, before_note_id=c.id)  # already just before c
    assert order(conn, deck) == ["d", "b", "c", "a"]


def test_move_to_other_deck_goes_to_the_end(conn, deck, player):
    other = repo.create_deck(conn, "other")
    add(conn, other, player, "o1")
    a, b = add(conn, deck, player, "a"), add(conn, deck, player, "b")
    assert repo.move_note(conn, a.id, deck_id=other.id) is True
    assert order(conn, deck) == ["b"]
    assert order(conn, other) == ["o1", "a"]
    assert repo.get_note(conn, a.id).deck_id == other.id
    assert repo.get_note(conn, b.id).deck_id == deck.id


def test_move_to_other_deck_before_a_note(conn, deck, player):
    other = repo.create_deck(conn, "other")
    o1 = add(conn, other, player, "o1")
    a = add(conn, deck, player, "a")
    repo.move_note(conn, a.id, deck_id=other.id, before_note_id=o1.id)
    assert order(conn, other) == ["a", "o1"]


def test_move_keeps_progress(conn, deck, player):
    other = repo.create_deck(conn, "other")
    n = repo.create_note(conn, deck.id, player.id, 2, {})
    conn.execute("INSERT INTO card_state VALUES (?, 1, 1.0, 'sm2', '{}')", (n.id,))
    repo.move_note(conn, n.id, deck_id=other.id)
    assert conn.execute("SELECT COUNT(*) FROM card_state").fetchone()[0] == 1


def test_move_errors(conn, deck, player):
    n = add(conn, deck, player)
    assert repo.move_note(conn, "nope") is False
    with pytest.raises(ValueError, match="Deck not found"):
        repo.move_note(conn, n.id, deck_id="nope")


def test_reorder_deck(conn, deck, player):
    a, b, c = (add(conn, deck, player, t) for t in "abc")
    repo.reorder_deck(conn, deck.id, [c.id, a.id, b.id])
    assert order(conn, deck) == ["c", "a", "b"]
    assert positions(conn, deck) == [1024, 2048, 3072]
    repo.reorder_deck(conn, deck.id, [b.id, c.id, a.id])  # reorder again: no constraint clash
    assert order(conn, deck) == ["b", "c", "a"]


@pytest.mark.parametrize("which", ["missing", "duplicate", "extra"])
def test_reorder_requires_exact_set(conn, deck, player, which):
    a, b = add(conn, deck, player, "a"), add(conn, deck, player, "b")
    ids = {"missing": [a.id], "duplicate": [a.id, a.id], "extra": [a.id, b.id, "zzz"]}[which]
    with pytest.raises(ValueError, match="exactly once"):
        repo.reorder_deck(conn, deck.id, ids)
    assert order(conn, deck) == ["a", "b"]


# ------------------------------------------------------------------------------- media


def test_note_media_link_unlink_list(conn, deck, player, tmp_path):
    n = add(conn, deck, player)
    a = repo.add_media(conn, tmp_path / "m", io.BytesIO(b"a"), "image/png")
    b = repo.add_media(conn, tmp_path / "m", io.BytesIO(b"b"), "audio/mpeg")
    repo.link_note_media(conn, n.id, a.id)
    repo.link_note_media(conn, n.id, a.id)
    repo.link_note_media(conn, n.id, b.id)
    assert {m.id for m in repo.list_note_media(conn, n.id)} == {a.id, b.id}
    assert repo.unlink_note_media(conn, n.id, a.id) is True
    assert repo.unlink_note_media(conn, n.id, a.id) is False
    assert [m.id for m in repo.list_note_media(conn, n.id)] == [b.id]
    assert repo.get_media(conn, a.id) is not None
