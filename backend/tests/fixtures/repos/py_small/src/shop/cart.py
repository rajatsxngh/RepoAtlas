from __future__ import annotations

from decimal import Decimal

from shop.utils.helpers import format_price, round_money


class Cart:
    """A shopping cart."""

    def __init__(self) -> None:
        self.items: list[tuple[str, Decimal]] = []

    def add(self, name: str, price: Decimal) -> None:
        self.items.append((name, round_money(price)))

    @property
    def total(self) -> Decimal:
        return sum((price for _, price in self.items), Decimal(0))

    def _discount(self) -> Decimal:
        return Decimal(0)

    def describe(self) -> str:
        return format_price(self.total - self._discount())


class GiftCart(Cart):
    def add(self, name: str, price: Decimal) -> None:
        super().add(name, Decimal(0))
