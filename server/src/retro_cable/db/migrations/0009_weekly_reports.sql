-- The Sunday report (Phase 8): what happened in the week, kept so /admin can show it and the
-- next report can say what changed. `report` is JSON (retro_cable/report.py).
CREATE TABLE weekly_reports (
    id         INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    week_of    TEXT NOT NULL,
    report     TEXT NOT NULL
);
