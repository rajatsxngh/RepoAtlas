from mini.util import helper, twice


class Base:
    def __init__(self, n):
        self.n = helper(n)

    def run(self):
        return helper(self.n)


class Child(Base):
    def run(self):
        return twice(self.n)


def make():
    return Base(1)


def entry():
    b = make()
    return b.run() + helper(2)


def nested():
    from mini.util import unused

    return unused()
