import { describe, expect, it } from "vitest";
import { add, sum } from "../src/math";

describe("math", () => {
  it("adds", () => {
    expect(add(1, 2)).toBe(3);
  });

  it("sums", () => {
    expect(sum([1, 2])).toBe(3);
  });
});
