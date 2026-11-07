from decimal import Decimal

from shop.cart import Cart, GiftCart


def test_add_rounds_prices(cart: Cart) -> None:
    cart.add("tea", Decimal("1.005"))
    assert cart.total == Decimal("1.01")


class TestGiftCart:
    def test_gifts_are_free(self) -> None:
        cart = GiftCart()
        cart.add("card", Decimal("5"))
        assert cart.total == 0
