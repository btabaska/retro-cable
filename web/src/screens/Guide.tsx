import { useEffect, useMemo, useState } from "react";
import type { Guide as GuideData, TvApi } from "../api/client";
import { episodeLabel } from "../components/InfoBanner";
import { useKeys } from "../hooks/useKeys";
import { cellIndexAt, cellSize, HALF_HOUR_MS, layoutRow, WINDOW_MS, windowStartFor } from "../lib/guide";
import { kicker } from "../lib/program";
import { formatAirDate, formatClock, formatGuideDay, formatLongDate, formatShortTime, zonedParts } from "../lib/time";
import { yearLabel } from "../lib/years";

const VISIBLE_ROWS = 7;
const AHEAD_HOURS = 12;
const pad = (n: number) => String(n).padStart(2, "0");

interface GuideProps {
  api: TvApi;
  year: number;
  now: number;
  timeZone: string;
  channelId: string;
  onTune: (channelId: string) => void;
  onYear: () => void;
  onBack: () => void;
}

interface Focus {
  row: number;
  /** The time the focus follows when moving between rows. */
  anchor: number;
}

/** The 2000s digital-cable grid: 7 channels x 90 minutes, cells at true run times. */
export function Guide({ api, year, now, timeZone, channelId, onTune, onYear, onBack }: GuideProps) {
  const [opened] = useState(() => windowStartFor(now));
  const [data, setData] = useState<GuideData | null>(null);
  const [failed, setFailed] = useState(false);
  const [windowStart, setWindowStart] = useState(opened);
  const [focus, setFocus] = useState<Focus>({ row: 0, anchor: now });

  useEffect(() => {
    let live = true;
    const { date, time } = zonedParts(opened, timeZone);
    api.getGuide(year, date, time, AHEAD_HOURS + 1.5).then(
      (guide) => {
        if (!live) return;
        setData(guide);
        const row = Math.max(0, guide.channels.findIndex((r) => r.channel.id === channelId));
        setFocus((f) => ({ ...f, row }));
      },
      () => {
        if (live) setFailed(true);
      },
    );
    return () => {
      live = false;
    };
  }, [api, year, opened, timeZone, channelId]);

  const rangeEnd = opened + (AHEAD_HOURS + 1.5) * 3_600_000;
  const rows = useMemo(
    () => (data ? data.channels.map((r) => ({ channel: r.channel, cells: layoutRow(r.slots, opened, rangeEnd) })) : []),
    [data, opened, rangeEnd],
  );
  const windowEnd = windowStart + WINDOW_MS;
  const focusedRow = rows[focus.row];
  const focusedIndex = focusedRow ? cellIndexAt(focusedRow.cells, focus.anchor) : -1;
  const focusedCell = focusedRow?.cells[focusedIndex];

  const reveal = (start: number, end: number) => {
    let ws = windowStart;
    while (start >= ws + WINDOW_MS && ws + HALF_HOUR_MS <= opened + AHEAD_HOURS * 3_600_000) ws += HALF_HOUR_MS;
    while (end <= ws && ws > opened) ws -= HALF_HOUR_MS;
    setWindowStart(ws);
    return ws;
  };

  useKeys((event) => {
    const key = event.key;
    if (key === "Escape" || key === "Backspace" || key === "g") onBack();
    else if (key === " " || key === "p" || key === "y" || key === "MediaPlayPause") onYear();
    else if (key === "Enter" && focusedRow) onTune(focusedRow.channel.id);
    else if ((key === "ArrowUp" || key === "ArrowDown") && rows.length) {
      const row = Math.min(rows.length - 1, Math.max(0, focus.row + (key === "ArrowUp" ? -1 : 1)));
      setFocus({ row, anchor: focus.anchor });
    } else if ((key === "ArrowLeft" || key === "ArrowRight") && focusedRow) {
      const target = focusedRow.cells[focusedIndex + (key === "ArrowLeft" ? -1 : 1)];
      if (!target) return;
      const ws = reveal(target.start, target.end);
      if (target.end <= ws) return; // already at the start of the guide
      setFocus({ row: focus.row, anchor: Math.max(target.start, ws) });
    } else return;
    event.preventDefault();
  });

  const firstRow = Math.min(Math.max(0, focus.row - 3), Math.max(0, rows.length - VISIBLE_ROWS));
  const visible = rows.slice(firstRow, firstRow + VISIBLE_ROWS);
  const slot = focusedCell?.slot;

  return (
    <div className="guide" data-testid="guide">
      <header className="header">
        <div className="guide-heading">
          <div className="wordmark">GUIDE</div>
          <div className="tab tab-active">All Channels</div>
        </div>
        <div className="guide-clock">
          <div className="clock">
            <div className="clock-time">{formatClock(now, timeZone)}</div>
            <div className="clock-date">{formatLongDate(now, timeZone)}</div>
          </div>
          <div className="year-badge" data-testid="year-badge">
            {yearLabel(year)}
          </div>
        </div>
      </header>

      <section className="details" data-testid="guide-details">
        {focusedRow && focusedCell && slot ? (
          <>
            <div className="details-line">
              {pad(focusedRow.channel.number)} · {focusedRow.channel.call_sign}
              {focusedRow.channel.network ? ` ${focusedRow.channel.network}` : ""} ·{" "}
              {formatShortTime(focusedCell.start, timeZone)} – {formatClock(Date.parse(slot.end), timeZone)}
            </div>
            <div className="details-title" data-testid="details-title">
              {slot.title}
            </div>
            {slot.episode_title && <div className="details-episode">{slot.episode_title}</div>}
            <div className="details-badges">
              {slot.season !== null && slot.episode !== null && (
                <span className="badge badge-outline">
                  S{slot.season} · E{slot.episode}
                </span>
              )}
              {slot.new && <span className="badge badge-new">NEW</span>}
              {slot.kind === "movie" && <span className="badge badge-outline">MOVIE</span>}
              {slot.holiday_name && <span className="badge badge-holiday">{slot.holiday_name.toUpperCase()}</span>}
              {slot.transplant && (
                <span className="badge badge-outline badge-transplant" data-testid="details-transplant">
                  <span className="transplant-mark" aria-hidden="true" />
                  PRETEND PREMIERE
                </span>
              )}
              <span className="details-aired">
                {slot.kind === "filler"
                  ? "No programming scheduled"
                  : `${slot.new ? "New" : "Rerun"}${
                      slot.air_date
                        ? ` · first aired ${formatAirDate(slot.air_date)}${slot.transplant ? " (pretend)" : ""}`
                        : ""
                    }`}
              </span>
            </div>
            {slot.kind !== "filler" && (
              <div className="details-synopsis" data-testid="details-synopsis">
                {slot.description || episodeLabel(slot) || slot.title}
              </div>
            )}
          </>
        ) : (
          <div className="details-title">{failed ? "No signal" : "Loading the guide…"}</div>
        )}
      </section>

      <section className="grid" role="grid" aria-label="Program guide">
        <div className="grid-times" role="row">
          <div className="grid-day">{formatGuideDay(windowStart, timeZone)}</div>
          <div className="grid-timebar">
            {[0, 1, 2].map((i) => (
              <div key={i} className="grid-time">
                {formatClock(windowStart + i * HALF_HOUR_MS, timeZone)}
              </div>
            ))}
          </div>
        </div>
        {visible.map((row, i) => {
          const rowIndex = firstRow + i;
          return (
            <div key={row.channel.id} className="row" role="row" data-testid={`guide-row-${row.channel.id}`}>
              <div className={`channel${row.channel.id === channelId ? " channel-current" : ""}`}>
                <span className="number">{pad(row.channel.number)}</span>
                <span className="call-sign">{row.channel.call_sign}</span>
              </div>
              <div className="cells">
                {layoutRow(
                  row.cells.map((c) => c.slot),
                  windowStart,
                  windowEnd,
                ).map((cell) => {
                  const isFocused =
                    rowIndex === focus.row && focusedCell !== undefined && cell.start === focusedCell.start;
                  const size = cellSize(cell.width);
                  const classes = [
                    "cell",
                    isFocused ? "cell-focused" : "",
                    cell.end <= now ? "cell-past" : "",
                    size === "full" ? "" : `cell-${size}`,
                    cell.slot.holiday ? "cell-holiday" : "",
                  ].filter(Boolean);
                  return (
                    <div
                      key={cell.slot.start}
                      role="gridcell"
                      aria-selected={isFocused}
                      aria-label={size === "sliver" ? cell.slot.title : undefined}
                      className={classes.join(" ")}
                      style={{ left: `${cell.left * 100}%`, width: `${cell.width * 100}%` }}
                      onClick={() => {
                        onTune(row.channel.id);
                      }}
                    >
                      {size === "full" && (
                        <span className="time">
                          {cell.clippedStart ? `from ${formatShortTime(cell.start, timeZone)}` : formatShortTime(cell.start, timeZone)}
                          {kicker(cell.slot).map((part) => (
                            <span key={part.text} className={`kicker kicker-${part.tone}`}>
                              {" · "}
                              {part.text}
                            </span>
                          ))}
                        </span>
                      )}
                      {size !== "sliver" && (
                        <span className="title">
                          {cell.slot.transplant && <span className="transplant-mark" data-testid="transplant-mark" />}
                          {cell.slot.title}
                        </span>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          );
        })}
      </section>

      <footer className="footer">
        <span>
          <strong>ENTER</strong> Watch
        </span>
        <span>
          <strong>SPACE</strong> Change year
        </span>
        <span>
          <strong>ESC</strong> Back
        </span>
        <span className="footer-right">
          <span className="transplant-mark" aria-hidden="true" /> Pretend premiere
        </span>
      </footer>
    </div>
  );
}
