import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { App } from "./App";

const version = () => Promise.resolve({ version: "1.2.3", commit: "abc1234" });

describe("App", () => {
  it("shows three guide cells with the first focused", () => {
    render(<App loadVersion={version} />);
    const cells = screen.getAllByRole("gridcell");
    expect(cells).toHaveLength(3);
    expect(cells[0]).toHaveFocus();
  });

  it("moves focus with the arrow keys", async () => {
    const user = userEvent.setup();
    render(<App loadVersion={version} />);
    const cells = screen.getAllByRole("gridcell");
    await user.keyboard("{ArrowRight}");
    expect(cells[1]).toHaveFocus();
    await user.keyboard("{ArrowRight}{ArrowRight}");
    expect(cells[2]).toHaveFocus();
    await user.keyboard("{ArrowLeft}");
    expect(cells[1]).toHaveFocus();
  });

  it("shows the server version", async () => {
    render(<App loadVersion={version} />);
    expect(await screen.findByTestId("server-version")).toHaveTextContent("Server v1.2.3 · abc1234");
  });

  it("says when the server is unreachable", async () => {
    render(<App loadVersion={() => Promise.reject(new Error("down"))} />);
    expect(await screen.findByTestId("server-version")).toHaveTextContent("No signal");
  });
});
