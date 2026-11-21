import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { Lineup, TvApi } from "../api/client";
import { YearPicker } from "./YearPicker";

const lineup = (year: number): Lineup => ({
  year,
  date: `${year}-10-31`,
  weekday: "Saturday",
  now: `${year}-10-31T20:14:00-05:00`,
  timezone: "America/New_York",
  channels: [
    {
      id: "adult-swim",
      name: "Adult Swim",
      type: "network",
      number: 46,
      call_sign: "ADSM",
      network: null,
      on_air: true,
      library: false,
      launch: null,
    },
  ],
});

function api(getLineup: TvApi["getLineup"]): TvApi {
  return {
    getServerVersion: vi.fn(() => Promise.resolve({ version: "1", commit: "a" })),
    getLineup,
    getGuide: vi.fn(() => Promise.reject(new Error("unused"))),
    tune: vi.fn(() => Promise.reject(new Error("unused"))),
  };
}

describe("YearPicker", () => {
  it("lists the dial for the focused year", async () => {
    render(<YearPicker api={api(vi.fn((y: number) => Promise.resolve(lineup(y))))} year={1998} onPick={vi.fn()} onBack={vi.fn()} />);
    expect(await screen.findByText("Adult Swim")).toBeInTheDocument();
  });

  it("offers All Years after 2010", async () => {
    const onPick = vi.fn();
    const user = userEvent.setup();
    render(<YearPicker api={api(vi.fn((y: number) => Promise.resolve(lineup(y))))} year={1998} onPick={onPick} onBack={vi.fn()} />);
    await user.click(screen.getByRole("gridcell", { name: "All Years" }));
    expect(onPick).toHaveBeenCalledWith(0);
  });

  it("reaches All Years from the last row of years with the arrow keys", async () => {
    const onPick = vi.fn();
    const getLineup = vi.fn((y: number) => Promise.resolve(lineup(y)));
    const user = userEvent.setup();
    render(<YearPicker api={api(getLineup)} year={2006} onPick={onPick} onBack={vi.fn()} />);
    await user.keyboard("{ArrowDown}");
    expect(screen.getByRole("gridcell", { name: "All Years" })).toHaveAttribute("aria-selected", "true");
    expect(await screen.findByText("On the dial in All Years")).toBeInTheDocument();
    await user.keyboard("{ArrowUp}{Enter}");
    expect(onPick).toHaveBeenCalledWith(2004);
  });

  it("retries the lineup when a request fails instead of leaving the dial empty", async () => {
    const getLineup = vi.fn<TvApi["getLineup"]>();
    getLineup.mockRejectedValueOnce(new Error("channels request failed: 502"));
    getLineup.mockImplementation((y: number) => Promise.resolve(lineup(y)));
    render(<YearPicker api={api(getLineup)} year={1997} onPick={vi.fn()} onBack={vi.fn()} />);
    await waitFor(
      () => {
        expect(screen.getByTestId("year-dial")).toHaveTextContent("Adult Swim");
      },
      { timeout: 4000 },
    );
    expect(getLineup).toHaveBeenCalledTimes(2);
  });
});
