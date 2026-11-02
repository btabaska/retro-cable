import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { ChannelInfo, TunedSlot } from "../api/client";
import { InfoBanner } from "./InfoBanner";

const channel: ChannelInfo = {
  id: "disney",
  name: "Disney Channel",
  type: "network",
  number: 30,
  call_sign: "DSNY",
  network: null,
  on_air: true,
  library: false,
  launch: null,
};

const slot = (over: Partial<TunedSlot>): TunedSlot => ({
  kind: "episode",
  start: "1998-10-31T20:00:00-05:00",
  end: "1998-10-31T20:22:00-05:00",
  seconds: 1320,
  title: "Doug",
  show: "doug",
  episode_title: "Doug's Halloween",
  season: 2,
  episode: 3,
  air_date: "1992-10-25",
  new: false,
  transplant: false,
  holiday: null,
  holiday_name: null,
  media_url: null,
  ...over,
});

const renderBanner = (s: TunedSlot) =>
  render(
    <InfoBanner
      channel={channel}
      slot={s}
      next={null}
      now={Date.parse("1998-10-31T20:05:00-05:00")}
      year={1998}
      timeZone="America/New_York"
      onGuide={() => undefined}
      onYear={() => undefined}
    />,
  );

describe("InfoBanner", () => {
  it("shows NEW and the holiday as text badges", () => {
    renderBanner(slot({ new: true, holiday: "halloween", holiday_name: "Halloween" }));
    expect(screen.getByText("NEW")).toHaveClass("badge-new");
    expect(screen.getByTestId("banner-holiday")).toHaveTextContent("HALLOWEEN");
  });

  it("badges a pretend premiere after the holiday, with the CSS mark (M2 A7)", () => {
    renderBanner(slot({ transplant: true, holiday: "halloween", holiday_name: "Halloween" }));
    const badge = screen.getByTestId("banner-transplant");
    expect(badge).toHaveTextContent("PRETEND PREMIERE");
    expect(badge.querySelector(".transplant-mark")).not.toBeNull();
    expect(badge.textContent).not.toContain("◇");
    expect(screen.getByTestId("banner-holiday").compareDocumentPosition(badge)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
  });

  it("never marks a show without a pretend premiere", () => {
    renderBanner(slot({}));
    expect(screen.queryByTestId("banner-transplant")).toBeNull();
  });

  it("never badges a rerun", () => {
    renderBanner(slot({}));
    expect(screen.queryByText("NEW")).toBeNull();
    expect(screen.queryByTestId("banner-holiday")).toBeNull();
  });

  it("shows a movie's year and running time instead of S/E", () => {
    renderBanner(
      slot({
        kind: "movie",
        title: "Halloweentown",
        episode_title: "",
        season: null,
        episode: null,
        air_date: "1998-10-17",
        seconds: 85 * 60,
        end: "1998-10-31T21:25:00-05:00",
      }),
    );
    expect(screen.getByText("1998 · 1h 25m")).toBeInTheDocument();
  });
});
