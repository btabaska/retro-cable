import type { ChannelInfo, GuideSlot, TunedSlot } from "../api/client";
import { formatShortTime } from "../lib/time";

interface InfoBannerProps {
  channel: ChannelInfo;
  slot: TunedSlot | null;
  next: TunedSlot | null;
  now: number;
  year: number;
  timeZone: string;
  onGuide: () => void;
  onYear: () => void;
}

const pad = (n: number) => String(n).padStart(2, "0");

export function episodeLabel(slot: GuideSlot): string {
  const parts = [];
  if (slot.episode_title) parts.push(slot.episode_title);
  if (slot.season !== null && slot.episode !== null) parts.push(`S${slot.season} E${slot.episode}`);
  return parts.join(" · ");
}

/** Channel, what's on, progress and what's next: shown for 4 s on every tune. */
export function InfoBanner({ channel, slot, next, now, year, timeZone, onGuide, onYear }: InfoBannerProps) {
  const start = slot ? Date.parse(slot.start) : now;
  const end = slot ? Date.parse(slot.end) : now;
  const progress = end > start ? Math.min(1, Math.max(0, (now - start) / (end - start))) : 0;
  const upNext = next?.kind === "bumper" ? null : next;
  return (
    <div className="banner" data-testid="info-banner">
      <div className="banner-channel">
        <div className="banner-number" data-testid="banner-number">
          {pad(channel.number)}
        </div>
        <div className="banner-call">
          {channel.call_sign}
          {channel.network ? ` ${channel.network}` : ""}
        </div>
      </div>
      <div className="banner-main">
        <div className="banner-title-row">
          <span className="banner-title" data-testid="banner-title">
            {slot?.kind === "bumper" ? channel.name : (slot?.title ?? "Tuning…")}
          </span>
          {slot && slot.kind !== "bumper" && <span className="banner-episode">{episodeLabel(slot)}</span>}
          {slot?.new && <span className="badge badge-new">NEW</span>}
        </div>
        <div className="banner-progress-row">
          <span>{formatShortTime(start, timeZone)}</span>
          <div className="banner-progress">
            <div className="banner-progress-fill" style={{ width: `${progress * 100}%` }} />
          </div>
          <span>{formatShortTime(end, timeZone)}</span>
        </div>
        <div className="banner-next">
          {upNext && (
            <>
              <strong>Next {formatShortTime(Date.parse(upNext.start), timeZone)}</strong> {upNext.title}
            </>
          )}
          <span className="banner-hint">
            <strong>▲▼</strong> Channel <strong>ENTER</strong> Guide <strong>SPACE</strong> Last channel{" "}
            <strong>Y</strong> Year
          </span>
        </div>
      </div>
      <div className="banner-buttons">
        <button type="button" className="btn btn-blue" onClick={onGuide}>
          Guide
        </button>
        <button type="button" className="btn btn-gold" onClick={onYear}>
          Year {year}
        </button>
      </div>
    </div>
  );
}
