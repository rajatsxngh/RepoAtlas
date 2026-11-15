import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api/errors";
import { mockApi, renderWithApi } from "@/test/render";
import { behindText, FreshnessBanner } from "./FreshnessBanner";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

/** Mock mode's stale map: py-small three commits ago (snapshot 6); snapshot 1 is its head. */
const STALE = 6;
const CURRENT = 1;

async function renderBanner(snapshotId: number, setup = mockApi()) {
  const snapshot = await setup.api.getSnapshot(snapshotId);
  renderWithApi(<FreshnessBanner snapshot={snapshot} pollMs={20} />, { api: setup.api });
  return setup;
}

beforeEach(() => push.mockClear());

describe("behindText", () => {
  it("counts commits, and says less when the count is unknown", () => {
    expect(behindText({ commits_behind: 1 })).toBe("1 commit behind");
    expect(behindText({ commits_behind: 3 })).toBe("3 commits behind");
    expect(behindText({ commits_behind: 1234 })).toBe("1,234 commits behind");
    expect(behindText({ commits_behind: null })).toBe("behind");
    expect(behindText({ commits_behind: 0 })).toBe("behind");
  });
});

describe("FreshnessBanner", () => {
  it("shows nothing for a map built from the head", async () => {
    const { api } = await renderBanner(CURRENT);
    await waitFor(async () => expect((await api.getFreshness(CURRENT)).stale).toBe(false));
    expect(screen.queryByRole("region", { name: "Map freshness" })).toBeNull();
  });

  it("says how far behind a stale map is, with both commits", async () => {
    await renderBanner(STALE);
    const banner = await screen.findByRole("region", { name: "Map freshness" });
    expect(banner).toHaveTextContent("This map is 3 commits behind main");
    expect(banner).toHaveTextContent("built from 1a2b3c4, now at 3f9d2c7");
    expect(within(banner).getByRole("button", { name: "Rebuild" })).toBeEnabled();
  });

  it("rebuilds, follows the build, and opens the new map", async () => {
    const user = userEvent.setup();
    await renderBanner(STALE);
    const banner = await screen.findByRole("region", { name: "Map freshness" });
    await user.click(within(banner).getByRole("button", { name: "Rebuild" }));
    expect(await within(banner).findByRole("status")).toBeVisible();
    expect(within(banner).queryByRole("button", { name: "Rebuild" })).toBeNull();
    await waitFor(() => expect(push).toHaveBeenCalledWith(`/s/${CURRENT}`), { timeout: 5000 });
    expect(push).toHaveBeenCalledTimes(1);
    expect(within(banner).getByRole("status")).toHaveTextContent("opening the new map");
  });

  it("opens the head's map at once when it is already built", async () => {
    const user = userEvent.setup();
    const setup = mockApi();
    // Someone else rebuilt the repository meanwhile.
    const accepted = await setup.api.buildRepo("https://github.com/repoatlas-fixtures/py-small");
    await waitFor(async () =>
      expect((await setup.api.getJob(accepted.job_id!)).status).toBe("succeeded"),
    );
    await renderBanner(STALE, setup);
    const banner = await screen.findByRole("region", { name: "Map freshness" });
    await user.click(within(banner).getByRole("button", { name: "Rebuild" }));
    await waitFor(() => expect(push).toHaveBeenCalledWith(`/s/${CURRENT}`));
  });

  it("can be dismissed", async () => {
    const user = userEvent.setup();
    await renderBanner(STALE);
    const banner = await screen.findByRole("region", { name: "Map freshness" });
    await user.click(within(banner).getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByRole("region", { name: "Map freshness" })).toBeNull();
  });

  it("says why a rebuild could not start, and offers to try again", async () => {
    const user = userEvent.setup();
    const setup = mockApi();
    const build = vi
      .spyOn(setup.api, "buildRepo")
      .mockRejectedValueOnce(
        new ApiError(429, "rate_limited", "Too many build requests.", { retry_after_s: 42 }),
      );
    await renderBanner(STALE, setup);
    const banner = await screen.findByRole("region", { name: "Map freshness" });
    await user.click(within(banner).getByRole("button", { name: "Rebuild" }));
    expect(await within(banner).findByRole("alert")).toHaveTextContent(
      "The rebuild failed: Too many requests",
    );
    await user.click(within(banner).getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(push).toHaveBeenCalledWith(`/s/${CURRENT}`), { timeout: 5000 });
    expect(build).toHaveBeenCalledTimes(2);
  });

  it("shows nothing when the repository can't be asked", async () => {
    const setup = mockApi();
    const asked = vi
      .spyOn(setup.api, "getFreshness")
      .mockRejectedValue(new ApiError(502, "upstream_unavailable", "GitHub did not answer."));
    await renderBanner(STALE, setup);
    await waitFor(() => expect(asked).toHaveBeenCalled());
    expect(screen.queryByRole("region", { name: "Map freshness" })).toBeNull();
  });

  it("says behind without a number when the count is unknown", async () => {
    const setup = mockApi();
    const real = await setup.api.getFreshness(STALE);
    vi.spyOn(setup.api, "getFreshness").mockResolvedValue({ ...real, commits_behind: null });
    await renderBanner(STALE, setup);
    const banner = await screen.findByRole("region", { name: "Map freshness" });
    expect(banner).toHaveTextContent("This map is behind main");
  });
});
