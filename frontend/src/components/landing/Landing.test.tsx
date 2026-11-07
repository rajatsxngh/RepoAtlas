import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { mockApi, renderWithApi } from "@/test/render";
import { Landing } from "./Landing";
import { RepoForm } from "./RepoForm";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

beforeEach(() => push.mockReset());

describe("RepoForm", () => {
  it("hints after the field is left, and offers a fix", async () => {
    const onSubmit = vi.fn();
    const user = userEvent.setup();
    renderWithApi(<RepoForm onSubmit={onSubmit} />);
    const input = screen.getByLabelText("GitHub repository URL");
    await user.type(input, "pallets/flask");
    expect(screen.queryByText(/did you mean/)).not.toBeInTheDocument();
    await user.tab();
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByText(/did you mean https:\/\/github.com\/pallets\/flask/)).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Use https://github.com/pallets/flask" }));
    expect(onSubmit).toHaveBeenCalledWith("https://github.com/pallets/flask");
  });

  it("refuses to submit an invalid URL and confirms a valid one", async () => {
    const onSubmit = vi.fn();
    const user = userEvent.setup();
    renderWithApi(<RepoForm onSubmit={onSubmit} />);
    const input = screen.getByLabelText("GitHub repository URL");
    await user.type(input, "https://gitlab.com/a/b{Enter}");
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByText(/Only https:\/\/github.com/)).toBeVisible();
    await user.clear(input);
    await user.type(input, "https://github.com/a/b.git");
    expect(screen.getByText("github.com/a/b")).toBeVisible();
    await user.click(screen.getByRole("button", { name: /Map it/ }));
    expect(onSubmit).toHaveBeenCalledWith("https://github.com/a/b");
  });

  it("waits out a rate limit and submits examples", async () => {
    const onSubmit = vi.fn();
    const user = userEvent.setup();
    const { rerender } = renderWithApi(
      <RepoForm
        onSubmit={onSubmit}
        waitS={12}
        examples={["o/r"]}
        initialValue="https://github.com/o/r"
      />,
    );
    expect(screen.getByRole("button", { name: "Wait 12 s" })).toBeDisabled();
    rerender(<RepoForm onSubmit={onSubmit} examples={["o/r"]} />);
    await user.click(screen.getByRole("button", { name: "o/r" }));
    expect(onSubmit).toHaveBeenCalledWith("https://github.com/o/r");
  });
});

describe("Landing", () => {
  it("builds, shows the stages, then opens the map", async () => {
    const user = userEvent.setup();
    renderWithApi(<Landing pollMs={15} />);
    await user.type(
      screen.getByLabelText("GitHub repository URL"),
      "https://github.com/repoatlas-fixtures/ts-small{Enter}",
    );
    const progress = await screen.findByRole("region", { name: "Building the map" });
    expect(within(progress).getByText("repoatlas-fixtures/ts-small")).toBeVisible();
    expect(within(progress).getByRole("list", { name: "Build stages" })).toHaveTextContent(
      "Parsing the code",
    );
    await waitFor(() => expect(push).toHaveBeenCalledWith("/s/2"), { timeout: 3000 });
  });

  it("opens a ready snapshot at once", async () => {
    const user = userEvent.setup();
    const { api } = mockApi();
    await api.buildRepo("https://github.com/repoatlas-fixtures/py-small");
    await new Promise((resolve) => setTimeout(resolve, 200));
    renderWithApi(<Landing pollMs={15} />, { api });
    await user.type(
      screen.getByLabelText("GitHub repository URL"),
      "https://github.com/repoatlas-fixtures/py-small{Enter}",
    );
    await waitFor(() => expect(push).toHaveBeenCalledWith("/s/1"));
    expect(screen.queryByRole("region", { name: /Building/ })).not.toBeInTheDocument();
  });

  it("explains a failed build and builds again on request", async () => {
    const user = userEvent.setup();
    renderWithApi(<Landing pollMs={15} />);
    await user.type(
      screen.getByLabelText("GitHub repository URL"),
      "https://github.com/someone/broken{Enter}",
    );
    expect(await screen.findByText(/refuses to build/, {}, { timeout: 3000 })).toBeVisible();
    expect(screen.getByRole("heading", { name: /Couldn't build the map/ })).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("heading", { name: /Building the map/ })).toBeVisible();
  });

  it.each([
    ["private", "This repository is private"],
    ["too-large", "This repository is too large"],
    ["missing", "Repository not found"],
  ])("explains why %s can't be mapped", async (name, title) => {
    const user = userEvent.setup();
    renderWithApi(<Landing />);
    await user.type(
      screen.getByLabelText("GitHub repository URL"),
      `https://github.com/o/${name}{Enter}`,
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(title);
    expect(push).not.toHaveBeenCalled();
  });

  it("counts down a rate limit before allowing another try", async () => {
    const user = userEvent.setup();
    renderWithApi(<Landing />);
    await user.type(
      screen.getByLabelText("GitHub repository URL"),
      "https://github.com/o/rate-limited{Enter}",
    );
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Too many requests");
    expect(alert).toHaveTextContent(/You can try again in 4[12] s/);
    expect(screen.getByRole("button", { name: /Wait 4[12] s/ })).toBeDisabled();
  });
});
