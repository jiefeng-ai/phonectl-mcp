import pytest

from phonectl.keys import KEYS, MODS, parse_key


def test_plain_key():
    assert parse_key("enter") == (0, KEYS["enter"])


def test_chord_is_case_and_space_insensitive():
    assert parse_key(" Cmd + Shift + A ") == (MODS["cmd"] | MODS["shift"], KEYS["a"])


@pytest.mark.parametrize("spec", ["", "+", "hyper+a", "cmd+f13"])
def test_rejects_unknown(spec):
    with pytest.raises(ValueError):
        parse_key(spec)
