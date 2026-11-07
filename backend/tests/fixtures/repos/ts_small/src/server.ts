import express from "express";
import type { Request, Response } from "express";
import { Cart, formatPrice } from "./cart";

const app = express();
const carts = new Map<string, Cart>();

app.get("/carts/:id", getCart);
app.post("/carts/:id/items", (req: Request, res: Response) => {
  carts.get(req.params.id)?.add(req.body);
  res.sendStatus(204);
});

async function getCart(req: Request, res: Response): Promise<void> {
  const cart = carts.get(req.params.id) ?? Cart.empty();
  res.json({ total: formatPrice(cart.total) });
}

export function createServer() {
  return app;
}

export const loadAdmin = () => import("./admin");
