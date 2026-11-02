import { useEffect, useRef } from "react";
import type { TunedSlot } from "../api/client";

export type PlayerMode = "live" | "pip" | "hidden";

interface PlayerProps {
  slot: TunedSlot | null;
  next: TunedSlot | null;
  callSign: string;
  mode: PlayerMode;
  muted: boolean;
  /** Simulated now in ms; the player seeks to (now - slot start). */
  simNow: () => number;
  onEnded: () => void;
}

const isDemoFeed = (url: string | null) => url?.includes("/media/_demo/") ?? false;

/** Native <video> at the offset /tune returned, or a card for filler. Advances at slot end. */
export function Player({ slot, next, callSign, mode, muted, simNow, onEnded }: PlayerProps) {
  const video = useRef<HTMLVideoElement>(null);
  const ended = useRef(onEnded);
  useEffect(() => {
    ended.current = onEnded;
  });

  const key = slot ? `${slot.start}|${slot.media_url ?? slot.title}` : "none";
  const start = slot ? Date.parse(slot.start) : 0;
  const end = slot ? Date.parse(slot.end) : 0;

  // Advance on the schedule, not on the file: the next slot starts exactly at this one's end.
  useEffect(() => {
    if (!slot) return;
    const id = window.setTimeout(() => {
      ended.current();
    }, Math.max(0, end - simNow()) + 50);
    return () => {
      window.clearTimeout(id);
    };
  }, [key, slot, end, simNow]);

  useEffect(() => {
    if (video.current) video.current.muted = muted;
  }, [muted]);

  const onLoaded = () => {
    const el = video.current;
    if (!el) return;
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
    <div className={`player player--${mode}`} data-testid="player" data-slot-title={slot?.title ?? ""}>
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
