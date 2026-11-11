import pytest

from slackgif.config import parse_size
from slackgif.errors import UsageError


def test_binary_units():
    assert parse_size("128KB") == 131_072
    assert parse_size("5MB") == 5_242_880
    assert parse_size("1.5MB") == 1_572_864
    assert parse_size("5120") == 5_242_880
    assert parse_size("1GB") == 1_073_741_824
    assert parse_size("1024B") == 1024


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "abc", ""])
def test_invalid(value):
    with pytest.raises(UsageError):
        parse_size(value)


@pytest.mark.parametrize("value", ["0.0001", "0.5B"])
def test_too_small_to_represent(value):
    with pytest.raises(UsageError):
        parse_size(value)
