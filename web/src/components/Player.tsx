import { useEffect, useRef, useState } from "react";
import type { TunedSlot } from "../api/client";
import { formatShortTime } from "../lib/time";

export type PlayerMode = "live" | "pip" | "hidden";

interface PlayerProps {
  slot: TunedSlot | null;
  next: TunedSlot | null;
  /** "02 KTVU" */
  callSign: string;
  channelName?: string;
  timeZone?: string;
  mode: PlayerMode;
  muted: boolean;
  /** Simulated now in ms; the player seeks to (now - slot start). */
  simNow: () => number;
  onEnded: () => void;
}

const isDemoFeed = (url: string | null) => url?.includes("/media/_demo/") ?? false;

/**
 * Programs always air in full. When the schedule says a slot is over but the file is still
 * playing a few seconds behind (seek rounding, buffering), let it finish, then advance.
 */
export const OVERRUN_TOLERANCE_MS = 5000;
/** A file whose real length differs from its slot by more than this is reported. */
export const DURATION_MISMATCH_S = 3;

/** Native <video> at the offset /tune returned, or a card for filler. Advances at slot end. */
export function Player({
  slot,
  next,
  callSign,
  channelName = "",
  timeZone = "America/New_York",
  mode,
  muted,
  simNow,
  onEnded,
}: PlayerProps) {
  const video = useRef<HTMLVideoElement>(null);
  const ended = useRef(onEnded);
  useEffect(() => {
    ended.current = onEnded;
  });
  const [mismatch, setMismatch] = useState<{ key: string; seconds: number } | null>(null);

  const key = slot ? `${slot.start}|${slot.media_url ?? slot.title}` : "none";
  const start = slot ? Date.parse(slot.start) : 0;
  const end = slot ? Date.parse(slot.end) : 0;

  // Advance on the schedule: the next slot starts at this one's end. The server sizes every
  // slot to the program's full runtime, so this never cuts a program; if the file is still
  // playing a little past the end because of drift, it finishes first.
  useEffect(() => {
    if (!slot) return;
    let done = false;
    let tail = 0;
    let el: HTMLVideoElement | null = null;
    const finish = () => {
      if (done) return;
      done = true;
      ended.current();
    };
    const id = window.setTimeout(() => {
      el = video.current;
      const left =
        el && !el.paused && !el.ended && Number.isFinite(el.duration) ? el.duration - el.currentTime : 0;
      if (el && left > 0 && left * 1000 <= OVERRUN_TOLERANCE_MS) {
        el.addEventListener("ended", finish, { once: true });
        tail = window.setTimeout(finish, left * 1000 + 250);
      } else {
        finish();
      }
    }, Math.max(0, end - simNow()) + 50);
    return () => {
      window.clearTimeout(id);
      window.clearTimeout(tail);
      el?.removeEventListener("ended", finish);
    };
  }, [key, slot, end, simNow]);

  useEffect(() => {
    if (video.current) video.current.muted = muted;
  }, [muted]);

  const onLoaded = () => {
    const el = video.current;
    if (!el) return;
    if (slot && Number.isFinite(el.duration)) {
      const diff = el.duration - slot.seconds;
      if (Math.abs(diff) > DURATION_MISMATCH_S) {
        // Report, don't cut: the schedule should carry the file's real runtime. Title only; never the URL.
        console.warn(
          `[player] ${slot.title}${slot.season !== null && slot.episode !== null ? ` S${slot.season}E${slot.episode}` : ""}: ` +
            `file runs ${el.duration.toFixed(1)} s but its slot is ${slot.seconds} s (${diff > 0 ? "+" : ""}${diff.toFixed(1)} s)`,
        );
        setMismatch({ key, seconds: Math.round(diff) });
      }
    }
    const offset = Math.max(0, (simNow() - start) / 1000);
    el.currentTime = Math.min(offset, Math.max(0, el.duration - 0.25));
    el.play().catch(() => {
      // Autoplay with sound needs a gesture first; the power-on screen provides it.
    });
  };

  let overlay: string | null = null;
  let detail = "";
  if (slot && isDemoFeed(slot.media_url)) {
    if (slot.kind === "bumper") {
      overlay = callSign;
      detail = next ? `Up next: ${next.title}` : "Stay tuned";
    } else {
      overlay = slot.title;
      detail =
        slot.season !== null && slot.episode !== null
          ? `S${slot.season} · E${slot.episode} — ${slot.episode_title}`
          : slot.kind === "movie"
            ? "Feature presentation"
            : "";
    }
  }

  return (
    <div
      className={`player player--${mode}`}
      data-testid="player"
      data-slot-title={slot?.title ?? ""}
      data-duration-mismatch={mismatch?.key === key ? mismatch.seconds : undefined}
    >
      {slot?.media_url ? (
        <video
          key={key}
          ref={video}
          className="player-video"
          src={slot.media_url}
          playsInline
          muted={muted}
          preload="auto"
          onLoadedMetadata={onLoaded}
          data-testid="player-video"
        />
      ) : slot?.kind === "bumper" ? (
        // A bumper without media: the station ID slate, like a card between programs.
        <div className="player-bumper" data-testid="bumper-slate">
          <div className="bumper-number">{callSign}</div>
          {channelName && <div className="bumper-name">{channelName}</div>}
          {next && (
            <div className="bumper-next">
              Up next {formatShortTime(Date.parse(next.start), timeZone)} {next.title}
            </div>
          )}
        </div>
      ) : (
        <div className="player-filler" data-testid="player-filler">
          <div className="bars" />
          <div className="filler-title">{slot ? slot.title : "Tuning…"}</div>
        </div>
      )}
      {overlay && (
        <div className="player-slate">
          <div className="slate-title">{overlay}</div>
          {detail && <div className="slate-detail">{detail}</div>}
        </div>
      )}
    </div>
  );
}
