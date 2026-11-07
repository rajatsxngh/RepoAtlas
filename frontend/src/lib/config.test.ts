import { describe, expect, it } from "vitest";
import { DEFAULT_API_URL, DEFAULT_NODE_CAP, readConfig } from "./config";

describe("readConfig", () => {
  it("uses the local API and the default node cap when nothing is set", () => {
    expect(readConfig({})).toEqual({
      apiUrl: DEFAULT_API_URL,
      mock: false,
      nodeCap: DEFAULT_NODE_CAP,
    });
  });

  it("trims trailing slashes from the API origin", () => {
    expect(readConfig({ NEXT_PUBLIC_API_URL: " https://api.example.test/ " }).apiUrl).toBe(
      "https://api.example.test",
    );
  });

  it.each([
    ["1", true],
    ["true", true],
    ["YES", true],
    ["0", false],
    ["", false],
    ["no", false],
  ])("reads NEXT_PUBLIC_API_MOCK=%j as %s", (value, mock) => {
    expect(readConfig({ NEXT_PUBLIC_API_MOCK: value }).mock).toBe(mock);
  });

  it("clamps the node cap and ignores values that are not numbers", () => {
    expect(readConfig({ NEXT_PUBLIC_NODE_CAP: "120" }).nodeCap).toBe(120);
    expect(readConfig({ NEXT_PUBLIC_NODE_CAP: "3" }).nodeCap).toBe(20);
    expect(readConfig({ NEXT_PUBLIC_NODE_CAP: "999999" }).nodeCap).toBe(2000);
    expect(readConfig({ NEXT_PUBLIC_NODE_CAP: "lots" }).nodeCap).toBe(DEFAULT_NODE_CAP);
  });
});
