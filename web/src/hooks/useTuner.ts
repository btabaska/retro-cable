import { useCallback, useEffect, useRef, useState } from "react";
import type { TunedSlot, Tune, TvApi } from "../api/client";

const RETRY_MS = 5000;

interface Tuned {
  channelId: string;
  year: number;
  tune: Tune;
  fetchedAt: number;
  index: number;
}

export interface Tuner {
  /** The slot to play now, or null while tuning. */
  slot: TunedSlot | null;
  upcoming: TunedSlot[];
  tune: Tune | null;
  fetchedAt: number;
  noSignal: boolean;
  /** Move to the next slot when the current one ends; refetches when the queue runs low. */
  advance: () => void;
}

/** What is on `channelId` in `year`: the server's answer plus local progress through it. */
export function useTuner(api: TvApi, channelId: string | null, year: number): Tuner {
  const [state, setState] = useState<Tuned | null>(null);
  const [noSignal, setNoSignal] = useState(false);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    if (!channelId) return;
    let live = true;
    let retry = 0;
    api.tune(channelId, year).then(
      (tune) => {
        if (!live) return;
        setState({ channelId, year, tune, fetchedAt: Date.now(), index: 0 });
        setNoSignal(false);
      },
      () => {
        if (!live) return;
        setNoSignal(true);
        retry = window.setTimeout(() => {
          setNonce((n) => n + 1);
        }, RETRY_MS);
      },
    );
    return () => {
      live = false;
      window.clearTimeout(retry);
    };
  }, [api, channelId, year, nonce]);

  const current = state && state.channelId === channelId && state.year === year ? state : null;
  const queue = current ? [current.tune.current, ...current.tune.next] : [];

  const latest = useRef(state);
  useEffect(() => {
    latest.current = state;
  });

  const advance = useCallback(() => {
    const s = latest.current;
    if (!s) return;
    const next = Math.min(s.index + 1, s.tune.next.length);
    setState({ ...s, index: next });
    // Refill the queue before it runs out; the answer restarts at the slot playing now.
    if (next >= s.tune.next.length - 1) setNonce((n) => n + 1);
  }, []);

  return {
    slot: current ? (queue[current.index] ?? null) : null,
    upcoming: current ? queue.slice(current.index + 1) : [],
    tune: current?.tune ?? null,
    fetchedAt: current?.fetchedAt ?? 0,
    noSignal,
    advance,
  };
}
