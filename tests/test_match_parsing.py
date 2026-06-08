from armapply.match import _clamp_score


def test_clamp_score_int_in_range() -> None:
    assert _clamp_score(7) == 7


def test_clamp_score_too_high() -> None:
    assert _clamp_score(99) == 10


def test_clamp_score_too_low() -> None:
    assert _clamp_score(0) == 1
    assert _clamp_score(-5) == 1


def test_clamp_score_string() -> None:
    assert _clamp_score("8") == 8


def test_clamp_score_garbage() -> None:
    assert _clamp_score("not a number") == 0
    assert _clamp_score(None) == 0
