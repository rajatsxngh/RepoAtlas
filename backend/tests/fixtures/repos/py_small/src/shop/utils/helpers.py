from decimal import ROUND_HALF_UP, Decimal


def round_money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def format_price(value: Decimal) -> str:
    def with_symbol(text: str) -> str:
        return f"${text}"

    return with_symbol(str(round_money(value)))


def chunks(items: list[int], size: int):
    for start in range(0, len(items), size):
        yield items[start : start + size]


try:
    from orjson import dumps
except ImportError:

    def dumps(obj: object) -> bytes:
        import json

        return json.dumps(obj).encode()
