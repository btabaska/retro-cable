import { useCallback, useEffect, useState } from "react";
import type { AdminApi, StationRuns } from "./api";
import { changeLabel, powerLabel, statusLabel, watchedLabel, weekLabel } from "./station";

interface StationPanelProps {
  api: AdminApi;
  /** After an undo here, so the rest of /admin refreshes. */
  onUndone: (message: string) => void;
}

type Status = { kind: "loading" } | { kind: "ready"; data: StationRuns } | { kind: "error" };

/** The Station tab: what the weekly station manager changed and why, newest first. The latest
 * applied run can be undone in one tap, like any /admin save. */
export function StationPanel({ api, onUndone }: StationPanelProps) {
  const [status, setStatus] = useState<Status>({ kind: "loading" });
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api.getStationRuns().then(
      (data) => {
        setStatus({ kind: "ready", data });
      },
      () => {
        setStatus({ kind: "error" });
      },
    );
  }, [api]);

  useEffect(() => {
    load();
  }, [load]);

  const undo = async () => {
    setBusy(true);
    try {
      const result = await api.undo();
      onUndone(result.message);
    } catch {
      onUndone("Nothing to undo");
    }
    setBusy(false);
    load();
  };

  if (status.kind === "loading") {
    return <div className="adm-card adm-skeleton" aria-busy="true" aria-label="Loading station manager runs" />;
  }
  if (status.kind === "error") {
    return (
      <div className="adm-card adm-error-card" role="alert">
        Couldn&apos;t load the station manager&apos;s runs.
      </div>
    );
  }
  const { runs, auto_apply } = status.data;
  return (
    <>
      <p className="adm-note" data-testid="station-mode">
        Runs Sundays at 2 AM from the last 4 weeks of viewing.{" "}
        {auto_apply ? "Changes apply on their own; undo the latest here." : "Auto-apply is off: runs only propose."}
      </p>
      {runs.length === 0 ? (
        <p className="adm-empty">The station manager hasn&apos;t run yet.</p>
      ) : (
        <ul className="adm-rows" data-testid="station-runs">
          {runs.map((run) => {
            const changes = run.report.changes ?? [];
            const power = run.report.power_hours ?? [];
            const dropped = run.report.dropped ?? [];
            return (
              <li key={run.id}>
                <section className="adm-card">
                  <div className="adm-section-head">
                    <strong>{weekLabel(run)}</strong>
                    <span className={run.status === "failed" ? "adm-fail" : "adm-hint"}>{statusLabel(run)}</span>
                  </div>
                  <p className="adm-note">{run.summary}</p>
                  <p className="adm-hint">{watchedLabel(run)}</p>
                  {run.report.model_summary && <p className="adm-note">“{run.report.model_summary}”</p>}
                  {(changes.length > 0 || power.length > 0) && (
                    <ul className="adm-station-changes">
                      {power.map((p) => (
                        <li key={`power-${p.channel}`}>
                          <span className="adm-hint">{p.channel}</span> {powerLabel(p)}
                        </li>
                      ))}
                      {changes.map((c) => (
                        <li key={c.knob}>
                          <span className="adm-hint">{c.channel}</span> {changeLabel(c)}
                          {c.why && <span className="adm-hint"> · {c.why}</span>}
                        </li>
                      ))}
                    </ul>
                  )}
                  {dropped.length > 0 && (
                    <p className="adm-hint">
                      Left out: {dropped.map((d) => `${d.what} (${d.reason})`).join("; ")}
                    </p>
                  )}
                  {run.can_undo && (
                    <button
                      type="button"
                      className="adm-btn adm-btn-outline"
                      disabled={busy}
                      onClick={() => {
                        void undo();
                      }}
                    >
                      Undo this week&apos;s changes
                    </button>
                  )}
                </section>
              </li>
            );
          })}
        </ul>
      )}
    </>
  );
}
