import { useEffect, useState } from "react";
import { type AdminApi, type MediaProgress, NotAvailable } from "./api";
import { encoderLine, etaLine, percent, POLL_MS, sortShows } from "./media";

interface MediaPanelProps {
  api: AdminApi;
}

type Status = { kind: "loading" } | { kind: "ready"; data: MediaProgress } | { kind: "missing" } | { kind: "error" };

/** The Media tab: how far the library conversion on the rig has got. No paths, hosts or URLs:
 * only counts, show titles and the encoder in use. */
export function MediaPanel({ api }: MediaPanelProps) {
  const [status, setStatus] = useState<Status>({ kind: "loading" });

  useEffect(() => {
    let live = true;
    const load = () => {
      api.getMediaProgress().then(
        (data) => {
          if (live) setStatus({ kind: "ready", data });
        },
        (e: unknown) => {
          if (!live) return;
          setStatus((s) => (s.kind === "ready" ? s : { kind: e instanceof NotAvailable ? "missing" : "error" }));
        },
      );
    };
    load();
    const id = window.setInterval(load, POLL_MS);
    return () => {
      live = false;
      window.clearInterval(id);
    };
  }, [api]);

  if (status.kind === "loading") {
    return <div className="adm-card adm-skeleton" aria-busy="true" aria-label="Loading conversion status" />;
  }
  if (status.kind === "missing") {
    return <p className="adm-empty">Conversion status isn&apos;t available on this server yet.</p>;
  }
  if (status.kind === "error") {
    return (
      <div className="adm-card adm-error-card" role="alert">
        Couldn&apos;t reach the conversion status. Trying again every 10 seconds.
      </div>
    );
  }
  const p = status.data;
  if (p.total === 0) return <p className="adm-empty">Nothing to convert.</p>;
  const shows = sortShows(p.shows);
  return (
    <>
      <section className="adm-card" data-testid="media-summary">
        <div className="adm-big">{percent(p)}%</div>
        <p className="adm-note">
          {p.done.toLocaleString("en-US")} of {p.total.toLocaleString("en-US")} files · {etaLine(p)}
          {p.failed > 0 && <span className="adm-fail"> · {p.failed} failed</span>}
        </p>
        <div
          className="adm-bar"
          role="progressbar"
          aria-label="Converted"
          aria-valuemin={0}
          aria-valuemax={p.total}
          aria-valuenow={p.done}
        >
          <div className="adm-bar-fill" style={{ width: `${percent(p)}%` }} />
        </div>
        <span className="adm-hint">{encoderLine(p)}</span>
      </section>
      <ul className="adm-rows" data-testid="media-shows">
        {shows.map((s) => {
          const done = s.state === "done";
          const share = s.total === 0 ? 0 : (s.done / s.total) * 100;
          return (
            <li key={s.title}>
              <div className={`adm-row${done ? " adm-row-done" : ""}`}>
                <span className="adm-row-main">
                  <span className="adm-row-title">{s.title}</span>
                </span>
                {s.failed > 0 && <span className="adm-fail">{s.failed} failed</span>}
                <div className="adm-bar adm-bar-mini" aria-hidden="true">
                  <div className="adm-bar-fill" style={{ width: `${share}%` }} />
                </div>
                <span className="adm-count">
                  {done ? "✓ " : ""}
                  {s.done} / {s.total}
                </span>
              </div>
            </li>
          );
        })}
      </ul>
    </>
  );
}
