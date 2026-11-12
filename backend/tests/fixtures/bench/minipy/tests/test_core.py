from mini.core import Child, entry, make
from mini.util import twice


def test_entry():
    assert entry() == 6


def test_make():
    assert make() is not None


def test_child():
    assert Child(1).run() == 4


def test_twice():
    assert twice(1) == 3
