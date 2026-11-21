import { useEffect, useRef, useState } from "react";
import type { ChannelInfo, Lineup, TvApi } from "../api/client";
import { useKeys } from "../hooks/useKeys";
import { moveFocus } from "../lib/focus";
import { ALL_YEARS, FIRST_YEAR, LAST_YEAR, yearLabel } from "../lib/years";

const COLS = 7;
// 1990-2010 fill three rows of seven; All Years has the fourth row to itself.
const YEARS = [...Array.from({ length: LAST_YEAR - FIRST_YEAR + 1 }, (_, i) => FIRST_YEAR + i), ALL_YEARS];
const FETCH_DELAY_MS = 120;
const RETRY_MS = 1000;
const MAX_RETRY_MS = 5000;
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

/** Pick the year: 1990-2010 or All Years, with the dial for the focused one. */
export function YearPicker({ api, year, onPick, onBack }: YearPickerProps) {
  const [focused, setFocused] = useState(year);
  const [lineups, setLineups] = useState<Record<number, Lineup>>({});
  const [failures, setFailures] = useState(0);
  const grid = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (lineups[focused]) return;
    let live = true;
    // A failed request is retried with a growing delay, so a busy server never leaves the dial empty.
    const delay = failures === 0 ? FETCH_DELAY_MS : Math.min(RETRY_MS * failures, MAX_RETRY_MS);
    const id = window.setTimeout(() => {
      api.getLineup(focused).then(
        (lineup) => {
          if (!live) return;
          setLineups((all) => ({ ...all, [focused]: lineup }));
          setFailures(0);
        },
        () => {
          if (live) setFailures((n) => n + 1);
        },
      );
    }, delay);
    return () => {
      live = false;
      window.clearTimeout(id);
    };
  }, [api, focused, lineups, failures]);

  useKeys((event) => {
    const index = Math.max(0, YEARS.indexOf(focused));
    const rows = Math.ceil(YEARS.length / COLS);
    const next = moveFocus({ row: Math.floor(index / COLS), col: index % COLS }, event.key, { rows, cols: COLS });
    if (next) {
      setFocused(YEARS[Math.min(YEARS.length - 1, next.row * COLS + next.col)] ?? focused);
      setFailures(0);
    } else if (event.key === "Enter") onPick(focused);
    else if (event.key === "Escape" || event.key === "Backspace") onBack();
    else return;
    event.preventDefault();
  });

  // Focus starts on the current year; closing hands it back to the opener (App.tsx).
  useEffect(() => {
    grid.current?.querySelector<HTMLElement>('[aria-selected="true"]')?.focus();
  }, []);

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
            {lineup ? longDate(lineup) : yearLabel(focused)}
          </div>
          <div className="year-sub">
            {focused === ALL_YEARS
              ? "All Years: every channel on, airing every season of every show, whenever it first aired."
              : "Each channel plays what had aired by this date, new episodes and reruns mixed."}
          </div>
          <div className="year-grid" role="grid" aria-label="Years" ref={grid}>
            {YEARS.map((y) => (
              <button
                type="button"
                key={y}
                role="gridcell"
                aria-selected={y === focused}
                className={`year-cell${y === ALL_YEARS ? " year-cell-all" : ""}${y === focused ? " year-cell-focused" : ""}${y === year ? " year-cell-current" : ""}`}
                onClick={() => {
                  onPick(y);
                }}
                onMouseEnter={() => {
                  setFocused(y);
                  setFailures(0);
                }}
              >
                {yearLabel(y)}
              </button>
            ))}
          </div>
        </div>
        <div className="year-dial" data-testid="year-dial">
          <div className="year-dial-title">On the dial in {yearLabel(focused)}</div>
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
