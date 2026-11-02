import { useCallback, useEffect, useMemo, useState } from "react";
import { type Lineup, liveApi, type TvApi } from "./api/client";
import { Player, type PlayerMode } from "./components/Player";
import { useNow } from "./hooks/useNow";
import { useTuner } from "./hooks/useTuner";
import { stepChannel } from "./lib/channels";
import { load, save } from "./lib/storage";
import { simulatedNow } from "./lib/time";
import { Guide } from "./screens/Guide";
import { BANNER_MS, LiveTV } from "./screens/LiveTV";
import { PowerOn } from "./screens/PowerOn";
import { FIRST_YEAR, LAST_YEAR, YearPicker } from "./screens/YearPicker";
import { Stage } from "./Stage";

type Screen = "live" | "guide" | "year";
const DEFAULT_YEAR = 1998;
const RETRY_MS = 5000;

function storedYear(): number {
  const year = Number(load("rc.year"));
  return year >= FIRST_YEAR && year <= LAST_YEAR ? year : DEFAULT_YEAR;
}

interface AppProps {
  api?: TvApi;
}

export function App({ api = liveApi }: AppProps) {
  const [powered, setPowered] = useState(false);
  const [screen, setScreen] = useState<Screen>("live");
  const [year, setYear] = useState(storedYear);
  const [lineup, setLineup] = useState<{ data: Lineup; fetchedAt: number } | null>(null);
  const [lineupFailed, setLineupFailed] = useState(false);
  const [channelId, setChannelId] = useState<string | null>(() => load("rc.channel"));
  const [lastChannelId, setLastChannelId] = useState<string | null>(null);
  const [bannerUntil, setBannerUntil] = useState(0);
  const [retry, setRetry] = useState(0);
  const wallNow = useNow(250); // re-render for the clock, banner progress and guide; real time

  useEffect(() => {
    let live = true;
    let timer = 0;
    api.getLineup(year).then(
      (data) => {
        if (!live) return;
        setLineup({ data, fetchedAt: Date.now() });
        setLineupFailed(false);
      },
      () => {
        if (!live) return;
        setLineupFailed(true);
        timer = window.setTimeout(() => {
          setRetry((n) => n + 1);
        }, RETRY_MS);
      },
    );
    return () => {
      live = false;
      window.clearTimeout(timer);
    };
  }, [api, year, retry]);

  const dial = useMemo(
    () =>
      (lineup?.data.year === year ? lineup.data.channels : [])
        .filter((c) => c.on_air)
        .sort((a, b) => a.number - b.number),
    [lineup, year],
  );
  const dialIds = useMemo(() => dial.map((c) => c.id), [dial]);
  const tunedId = dial.length === 0 ? null : channelId && dialIds.includes(channelId) ? channelId : (dialIds[0] ?? null);
  const channel = dial.find((c) => c.id === tunedId) ?? null;

  const tuner = useTuner(api, powered ? tunedId : null, year);

  const timeZone = lineup?.data.timezone ?? "America/New_York";
  const simNow = useCallback(() => {
    if (tuner.tune) return simulatedNow(tuner.tune.now, tuner.fetchedAt, Date.now());
    if (lineup) return simulatedNow(lineup.data.now, lineup.fetchedAt, Date.now());
    return Date.now();
  }, [tuner.tune, tuner.fetchedAt, lineup]);
  const now = simNow();

  const showBanner = useCallback(() => {
    setBannerUntil(Date.now() + BANNER_MS);
  }, []);

  const tuneTo = useCallback(
    (id: string) => {
      if (id !== tunedId) {
        setLastChannelId(tunedId);
        setChannelId(id);
        save("rc.channel", id);
      }
      showBanner();
    },
    [tunedId, showBanner],
  );

  const pickYear = (y: number) => {
    setYear(y);
    save("rc.year", String(y));
    setScreen("live");
    showBanner();
  };

  let mode: PlayerMode = "hidden";
  if (powered && screen === "live") mode = "live";
  else if (powered && screen === "guide") mode = "pip";

  let body;
  if (!powered) {
    body = (
      <PowerOn
        year={year}
        loadVersion={api.getServerVersion}
        onPower={() => {
          setPowered(true);
          showBanner();
        }}
      />
    );
  } else if (lineupFailed && !lineup) {
    body = (
      <div className="no-signal" data-testid="no-signal">
        <div className="no-signal-card">NO SIGNAL</div>
        <div className="no-signal-sub">Can't reach the cable box server. Retrying…</div>
      </div>
    );
  } else if (screen === "year") {
    body = (
      <YearPicker
        api={api}
        year={year}
        onPick={pickYear}
        onBack={() => {
          setScreen("live");
        }}
      />
    );
  } else if (screen === "guide" && tunedId) {
    body = (
      <Guide
        api={api}
        year={year}
        now={now}
        timeZone={timeZone}
        channelId={tunedId}
        onTune={(id) => {
          tuneTo(id);
          setScreen("live");
        }}
        onYear={() => {
          setScreen("year");
        }}
        onBack={() => {
          setScreen("live");
        }}
      />
    );
  } else if (channel) {
    body = (
      <LiveTV
        channel={channel}
        dial={dial}
        slot={tuner.slot}
        next={tuner.upcoming[0] ?? null}
        now={now}
        wallNow={wallNow}
        year={year}
        timeZone={timeZone}
        bannerUntil={bannerUntil}
        onShowBanner={showBanner}
        onStep={(delta) => {
          if (tunedId) tuneTo(stepChannel(dialIds, tunedId, delta));
        }}
        onLast={() => {
          if (lastChannelId && dialIds.includes(lastChannelId)) tuneTo(lastChannelId);
        }}
        onNumber={tuneTo}
        onGuide={() => {
          setScreen("guide");
        }}
        onYear={() => {
          setScreen("year");
        }}
      />
    );
  } else {
    body = <div className="tuning">Tuning…</div>;
  }

  return (
    <Stage>
      {powered && (
        <Player
          slot={tuner.slot}
          next={tuner.upcoming.find((s) => s.kind !== "bumper") ?? null}
          callSign={channel ? `${String(channel.number).padStart(2, "0")} ${channel.call_sign}` : ""}
          mode={mode}
          muted={mode !== "live" && mode !== "pip"}
          simNow={simNow}
          onEnded={tuner.advance}
        />
      )}
      {body}
    </Stage>
  );
}
