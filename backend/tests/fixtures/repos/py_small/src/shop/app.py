from fastapi import FastAPI

from .cart import Cart

app = FastAPI()
_carts: dict[int, Cart] = {}


@app.get("/carts/{cart_id}")
async def get_cart(cart_id: int) -> dict[str, str]:
    cart = _carts.setdefault(cart_id, Cart())
    return {"total": cart.describe()}


@app.post("/carts/{cart_id}/items")
def add_item(cart_id: int, name: str, price: str) -> None:
    from decimal import Decimal

    _carts[cart_id].add(name, Decimal(price))


def create_app() -> FastAPI:
    return app
