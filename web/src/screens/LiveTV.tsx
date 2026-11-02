import { useEffect, useRef, useState } from "react";
import type { ChannelInfo, TunedSlot } from "../api/client";
import { Clock } from "../components/Clock";
import { InfoBanner } from "../components/InfoBanner";
import { useKeys } from "../hooks/useKeys";

export const BANNER_MS = 4000;
const DIGIT_MS = 1500;

interface LiveTVProps {
  channel: ChannelInfo;
  dial: ChannelInfo[];
  slot: TunedSlot | null;
  next: TunedSlot | null;
  /** Simulated now (ms): the clock, progress and schedule. */
  now: number;
  /** Real wall-clock now (Date.now()); the banner timer runs on it, never on simulated time. */
  wallNow: number;
  year: number;
  timeZone: string;
  /** The banner shows until this real wall-clock time (Date.now() ms); the app sets it on every tune. */
  bannerUntil: number;
  onShowBanner: () => void;
  onStep: (delta: 1 | -1) => void;
  onLast: () => void;
  onNumber: (channelId: string) => void;
  onGuide: () => void;
  onYear: () => void;
}

/** Full-screen TV: channel up/down, last channel, number entry and the info banner. */
export function LiveTV(props: LiveTVProps) {
  const { channel, dial, slot, next, now, wallNow, year, timeZone, bannerUntil } = props;
  const [digits, setDigits] = useState("");
  // The app re-renders every 250 ms for the clock, so the timer must not depend on `props`,
  // or it restarts on every render and number entry never commits.
  const onNumber = useRef(props.onNumber);
  useEffect(() => {
    onNumber.current = props.onNumber;
  });

  useEffect(() => {
    if (!digits) return;
    const id = window.setTimeout(() => {
      const match = dial.find((c) => c.number === Number(digits));
      if (match) onNumber.current(match.id);
      setDigits("");
    }, DIGIT_MS);
    return () => {
      window.clearTimeout(id);
    };
  }, [digits, dial]);

  useKeys((event) => {
    const key = event.key;
    if (key === "ArrowUp" || key === "PageUp") props.onStep(1);
    else if (key === "ArrowDown" || key === "PageDown") props.onStep(-1);
    else if (key === "Enter") props.onGuide();
    else if (key === " " || key === "p" || key === "MediaPlayPause") props.onLast();
    else if (key === "y" || key === "Y") props.onYear();
    else if (key === "Escape" || key === "i" || key === "ArrowLeft" || key === "ArrowRight")
      props.onShowBanner();
    else if (/^\d$/.test(key)) setDigits((d) => (d + key).slice(-3));
    else return;
    event.preventDefault();
  });

  // Real time vs real time: simulated `now` is years behind Date.now(), so comparing it would never hide.
  const showBanner = wallNow < bannerUntil;
  return (
    <div className="live" data-testid="live">
      {showBanner && <Clock now={now} timeZone={timeZone} className="clock clock-live" />}
      {digits && (
        <div className="digits" data-testid="digits">
          {digits}
          <span className="digits-cursor">_</span>
        </div>
      )}
      {showBanner && (
        <InfoBanner
          channel={channel}
          slot={slot}
          next={next}
          now={now}
          year={year}
          timeZone={timeZone}
          onGuide={props.onGuide}
          onYear={props.onYear}
        />
      )}
    </div>
  );
}
