import { useEffect, useState } from "react";
import type { AdminApi, Reports } from "./api";
import { backupLabel, conversionLabel, duration, libraryLabel, reportWeek, viewingLabel } from "./report";

interface ReportPanelProps {
  api: AdminApi;
}

type Status = { kind: "loading" } | { kind: "ready"; data: Reports } | { kind: "error" };

/** The Report tab: the latest Sunday report in full, then older ones as their headlines. */
export function ReportPanel({ api }: ReportPanelProps) {
  const [status, setStatus] = useState<Status>({ kind: "loading" });

  useEffect(() => {
    api.getReports().then(
      (data) => {
        setStatus({ kind: "ready", data });
      },
      () => {
        setStatus({ kind: "error" });
      },
    );
  }, [api]);

  if (status.kind === "loading") {
    return <div className="adm-card adm-skeleton" aria-busy="true" aria-label="Loading the Sunday report" />;
  }
  if (status.kind === "error") {
    return (
      <div className="adm-card adm-error-card" role="alert">
        Couldn&apos;t load the Sunday reports.
      </div>
    );
  }
  const { schedule, reports } = status.data;
  const [latest, ...older] = reports;
  if (!latest) {
    return <p className="adm-empty">The first report comes {schedule}.</p>;
  }
  const r = latest.report;
  const warnings = r.backups.schedule_warnings ?? [];
  const conversion = conversionLabel(r);
  const top = r.viewing.top_shows;
  return (
    <>
      <section className="adm-card" data-testid="report-latest">
        <div className="adm-section-head">
          <strong>{reportWeek(r.week_of)}</strong>
          <span className={r.backups.ok ? "adm-hint" : "adm-fail"}>{r.backups.ok ? "All good" : "Needs a look"}</span>
        </div>
        {r.pending.map((p) => (
          <p key={p} className="adm-note">
            Waits for you: {p}
          </p>
        ))}
        <h2 className="adm-report-head">Viewing</h2>
        <p className="adm-note">{viewingLabel(r)}</p>
        {top.length > 0 && (
          <ul className="adm-station-changes">
            {top.map((s) => (
              <li key={`${s.channel}-${s.show}`}>
                {s.title} on {s.channel} <span className="adm-hint">· {duration(s.seconds)}</span>
              </li>
            ))}
          </ul>
        )}
        <h2 className="adm-report-head">Station manager</h2>
        <p className="adm-note">{r.station ? r.station.summary : "Didn't run this week."}</p>
        <h2 className="adm-report-head">On the air</h2>
        <p className="adm-note">{libraryLabel(r)}</p>
        {r.library.empty_count > 0 && (
          <p className="adm-hint">
            Nothing yet for {r.library.empty_count} carried titles: {r.library.empty.join(", ")}
            {r.library.empty_count > r.library.empty.length ? ", …" : ""}
          </p>
        )}
        {conversion && (
          <>
            <h2 className="adm-report-head">Conversion</h2>
            <p className="adm-note">{conversion}</p>
          </>
        )}
        <h2 className="adm-report-head">Backups</h2>
        <p className={r.backups.ok ? "adm-note" : "adm-note adm-fail"}>{backupLabel(r)}</p>
        {warnings.length > 0 && (
          <ul className="adm-station-changes">
            {warnings.slice(0, 10).map((w) => (
              <li key={w} className="adm-hint">
                {w}
              </li>
            ))}
          </ul>
        )}
      </section>
      {older.length > 0 && (
        <ul className="adm-rows" data-testid="report-older">
          {older.map((o) => (
            <li key={o.id}>
              <section className="adm-card">
                <strong>{reportWeek(o.week_of)}</strong>: {o.report.headline.join(" ")}
              </section>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
