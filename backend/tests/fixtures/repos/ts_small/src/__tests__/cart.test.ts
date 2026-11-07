import { describe, expect, it } from "vitest";
import { Cart, sum } from "../cart";

describe("Cart", () => {
  it("adds items", () => {
    const cart = new Cart();
    cart.add({ name: "tea", price: 2 });
    expect(cart.total).toBe(2);
  });

  it("adds items", () => {
    expect(sum([1, 2])).toBe(3);
  });

  describe("empty", () => {
    it("has no total", () => {
      expect(Cart.empty().total).toBe(0);
    });
  });
});
