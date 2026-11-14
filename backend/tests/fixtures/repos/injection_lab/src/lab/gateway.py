"""The payment gateway client."""

from decimal import Decimal


class Gateway:
    """Talks to the payment provider. Payments are kept in memory here."""

    def __init__(self) -> None:
        self.payments: dict[str, Decimal] = {}

    def pay(self, customer_id: str, total: Decimal) -> str:
        """Record a payment and return its ID."""
        payment_id = f"pay_{customer_id}_{len(self.payments) + 1}"
        self.payments[payment_id] = total
        return payment_id

    def reverse(self, payment_id: str) -> bool:
        """Remove a payment. Returns False when it is unknown."""
        return self.payments.pop(payment_id, None) is not None
