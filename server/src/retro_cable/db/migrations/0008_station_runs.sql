-- Every run of the weekly station manager (M5): applied, proposed (auto-apply off), dry run,
-- skipped (not enough viewing, or nothing to change) or failed (model unreachable or invalid,
-- or the catalog changed under it). `report` is JSON (station_manager/runs.py): the signals
-- summary, the changes and why, and what was dropped. An applied run names the overlay
-- revision it saved, which one undo in /admin reverts.
CREATE TABLE station_runs (
    id          INTEGER PRIMARY KEY,
    created_at  TEXT NOT NULL,
    week_of     TEXT NOT NULL,
    status      TEXT NOT NULL
                CHECK (status IN ('applied', 'proposed', 'dry_run', 'skipped', 'failed')),
    model       TEXT NOT NULL DEFAULT '',
    revision_id INTEGER REFERENCES catalog_revisions (id),
    summary     TEXT NOT NULL,
    report      TEXT NOT NULL
);
