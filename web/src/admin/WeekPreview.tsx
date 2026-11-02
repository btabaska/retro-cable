import { useEffect, useState } from "react";
import type { AdminApi, ChannelDraft, Preview } from "./api";
import { dayChip, dayChipParts, FIRST_YEAR, LAST_YEAR } from "./draft";
import { Sheet } from "./Sheet";

interface WeekPreviewProps {
  api: AdminApi;
  draft: ChannelDraft;
  channelId: string | null;
  initialYear: number;
  onClose: () => void;
}

const YEARS = Array.from({ length: LAST_YEAR - FIRST_YEAR + 1 }, (_, i) => FIRST_YEAR + i);

/** "20:05:10" in the slot's own offset -> "8:05". The API sends local times. */
export function clockLabel(iso: string): string {
  const hh = Number(iso.slice(11, 13));
  const mm = iso.slice(14, 16);
  return `${hh % 12 === 0 ? 12 : hh % 12}:${mm}`;
}

function todayMonthDay(): string {
  const now = new Date();
  return `${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
}

/** A week of the draft's guide before it is saved, from the same engine. */
export function WeekPreview({ api, draft, channelId, initialYear, onClose }: WeekPreviewProps) {
  const [year, setYear] = useState(initialYear);
  const [data, setData] = useState<Preview | null>(null);
  const [failed, setFailed] = useState(false);
  const [picked, setPicked] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    api.preview({ draft, channel_id: channelId, year }).then(
      (p) => {
        if (!live) return;
        setData(p);
        const today = todayMonthDay();
        setPicked((prev) => {
          const keep = prev ? p.days.find((d) => d.date.slice(5) === prev.slice(5)) : undefined;
          return (keep ?? p.days.find((d) => d.date.slice(5) === today) ?? p.days[0])?.date ?? null;
        });
      },
      () => {
        if (live) setFailed(true);
      },
    );
    return () => {
      live = false;
    };
  }, [api, draft, channelId, year]);

  const day = data?.days.find((d) => d.date === picked) ?? null;
  const programs = day?.slots.filter((s) => s.kind !== "bumper") ?? [];

  return (
    <Sheet title="Preview week" onClose={onClose}>
      <div className="adm-sheet-head">
        <h2 className="adm-h2">Preview week</h2>
        <label className="adm-inline-label">
          Year
          <select
            className="adm-select"
            value={year}
            onChange={(e) => {
              setData(null);
              setFailed(false);
              setYear(Number(e.target.value));
            }}
          >
            {YEARS.map((y) => (
              <option key={y} value={y}>
                {y}
              </option>
            ))}
          </select>
        </label>
        <button type="button" className="adm-btn adm-btn-text" onClick={onClose}>
          Close
        </button>
      </div>
      <div className="adm-sheet-body" data-testid="week-preview">
        {failed ? (
          <div className="adm-card adm-error-card" role="alert">
            Couldn&apos;t build the preview.
          </div>
        ) : !data ? (
          <div className="adm-rows" aria-busy="true">
            {Array.from({ length: 5 }, (_, i) => (
              <div key={i} className="adm-row adm-skeleton" />
            ))}
          </div>
        ) : !data.check.ok ? (
          <div className="adm-card adm-error-card" role="alert">
            <h3 className="adm-h3">Can&apos;t preview: {data.check.errors.length} problems</h3>
            <ul>
              {data.check.errors.map((e) => (
                <li key={e.message}>{e.message}</li>
              ))}
            </ul>
          </div>
        ) : (
          <>
            <div className="adm-chips" role="tablist" aria-label="Day">
              {data.days.map((d) => (
                <button
                  key={d.date}
                  type="button"
                  role="tab"
                  aria-selected={d.date === picked}
                  aria-label={dayChip(d.date)}
                  className={`adm-chip${d.date === picked ? " adm-chip-on" : ""}`}
                  onClick={() => {
                    setPicked(d.date);
                  }}
                >
                  <span className="adm-chip-day">{dayChipParts(d.date).day}</span>
                  <span className="adm-chip-date">{dayChipParts(d.date).date}</span>
                </button>
              ))}
            </div>
            {day && !day.on_air ? (
              <p className="adm-empty">Not on the dial yet on this date.</p>
            ) : (
              <ol className="adm-schedule" data-testid="preview-day">
                {programs.map((s) => (
                  <li key={s.start} className="adm-schedule-row">
                    <span className="adm-schedule-time">{clockLabel(s.start)}</span>
                    <span className="adm-row-main">
                      <span className="adm-row-title">
                        {s.title}
                        {s.new && <span className="adm-tv-chip adm-tv-new">NEW</span>}
                        {s.holiday_name && (
                          <span className="adm-tv-chip adm-tv-holiday">{s.holiday_name.toUpperCase()}</span>
                        )}
                      </span>
                      <span className="adm-row-meta">
                        {s.kind === "movie"
                          ? `MOVIE${s.air_date ? ` · ${s.air_date.slice(0, 4)}` : ""}`
                          : s.kind === "filler"
                            ? "No programming"
                            : s.episode_title}
                      </span>
                    </span>
                  </li>
                ))}
              </ol>
            )}
          </>
        )}
      </div>
    </Sheet>
  );
}
