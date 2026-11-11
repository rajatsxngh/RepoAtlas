"""Arithmetic helpers used by the sandbox tests."""


def add(a, b):
    total = a + b
    return total


def double(a):
    result = add(a, a)
    return result


class Counter:
    def __init__(self):
        self.value = 0

    def bump(self, by=1):
        self.value = add(self.value, by)
        return self.value


def unused(x):
    y = x * 3
    return y
