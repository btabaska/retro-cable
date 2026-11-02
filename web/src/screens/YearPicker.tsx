import { useEffect, useState } from "react";
import type { ChannelInfo, Lineup, TvApi } from "../api/client";
import { useKeys } from "../hooks/useKeys";
import { moveFocus } from "../lib/focus";

export const FIRST_YEAR = 1990;
export const LAST_YEAR = 2010;
const COLS = 7;
const YEARS = Array.from({ length: LAST_YEAR - FIRST_YEAR + 1 }, (_, i) => FIRST_YEAR + i);
const pad = (n: number) => String(n).padStart(2, "0");

interface YearPickerProps {
  api: TvApi;
  year: number;
  onPick: (year: number) => void;
  onBack: () => void;
}

function note(channel: ChannelInfo): string {
  if (!channel.on_air) return `Hidden until ${channel.launch?.slice(0, 4) ?? "later"}`;
  if (channel.type === "movie") return "Your playlist";
  if (channel.library) return "Always on (classics)";
  return "On air";
}

function longDate(lineup: Lineup): string {
  const [y, m, d] = lineup.date.split("-").map(Number);
  const month = new Date(Date.UTC(y ?? 2000, (m ?? 1) - 1, d ?? 1)).toLocaleString("en-US", {
    month: "long",
    timeZone: "UTC",
  });
  return `${lineup.weekday}, ${month} ${d ?? ""}, ${y ?? ""}`;
}

/** Pick the year: 1990-2010, with the dial for the focused year. */
export function YearPicker({ api, year, onPick, onBack }: YearPickerProps) {
  const [focused, setFocused] = useState(year);
  const [lineups, setLineups] = useState<Record<number, Lineup>>({});

  useEffect(() => {
    if (lineups[focused]) return;
    let live = true;
    const id = window.setTimeout(() => {
      api.getLineup(focused).then(
        (lineup) => {
          if (live) setLineups((all) => ({ ...all, [focused]: lineup }));
        },
        () => undefined,
      );
    }, 120);
    return () => {
      live = false;
      window.clearTimeout(id);
    };
  }, [api, focused, lineups]);

  useKeys((event) => {
    const index = focused - FIRST_YEAR;
    const rows = Math.ceil(YEARS.length / COLS);
    const next = moveFocus({ row: Math.floor(index / COLS), col: index % COLS }, event.key, { rows, cols: COLS });
    if (next) {
      setFocused(Math.min(LAST_YEAR, FIRST_YEAR + next.row * COLS + next.col));
    } else if (event.key === "Enter") onPick(focused);
    else if (event.key === "Escape" || event.key === "Backspace") onBack();
    else return;
    event.preventDefault();
  });

  const lineup = lineups[focused];
  return (
    <div className="year-picker" data-testid="year-picker">
      <header className="header">
        <div className="wordmark">SET YOUR YEAR</div>
        <button type="button" className="btn btn-blue" onClick={onBack}>
          Back
        </button>
      </header>
      <div className="year-body">
        <div className="year-left">
          <div className="year-kicker">Today on your TV is</div>
          <div className="year-date" data-testid="year-date">
            {lineup ? longDate(lineup) : `${focused}`}
          </div>
          <div className="year-sub">Each channel plays what had aired by this date, new episodes and reruns mixed.</div>
          <div className="year-grid" role="grid" aria-label="Years">
            {YEARS.map((y) => (
              <button
                type="button"
                key={y}
                role="gridcell"
                aria-selected={y === focused}
                className={`year-cell${y === focused ? " year-cell-focused" : ""}${y === year ? " year-cell-current" : ""}`}
                onClick={() => {
                  onPick(y);
                }}
                onMouseEnter={() => {
                  setFocused(y);
                }}
              >
                {y}
              </button>
            ))}
          </div>
        </div>
        <div className="year-dial" data-testid="year-dial">
          <div className="year-dial-title">On the dial in {focused}</div>
          {lineup?.channels
            .slice()
            .sort((a, b) => a.number - b.number)
            .map((c) => (
              <div key={c.id} className={`dial-row${c.on_air ? "" : " dial-row-off"}`}>
                <span className="dial-number">{pad(c.number)}</span>
                <span className="dial-name">{c.name}</span>
                <span className="dial-note">{note(c)}</span>
              </div>
            ))}
        </div>
      </div>
    </div>
  );
}
