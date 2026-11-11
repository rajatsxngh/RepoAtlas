import pytest

from mini.calc import Counter, add, double


def test_add():
    assert add(2, 3) == 5


@pytest.mark.parametrize("n", [1, 2])
def test_double(n):
    assert double(n) == 2 * n


class TestCounter:
    def test_bump(self):
        counter = Counter()
        assert counter.bump() == 1


def test_skipped():
    pytest.skip("always skipped")
