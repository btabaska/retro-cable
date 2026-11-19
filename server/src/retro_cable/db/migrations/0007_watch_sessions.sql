-- What was watched (M5), for the weekly station manager. One row per program a viewer stayed
-- on: the web app reports it while it plays (heartbeats update the same row by `session`)
-- and once more when it ends. Privacy-boring on purpose: no IP addresses, tokens, URLs or
-- file paths; `device` is an app-chosen label and NULL until the apps have one.
--   sim_date        the simulated date when the session began (chosen year + that day's date)
--   started_at      real UTC wall-clock time the session began, stamped by the server
--   offset_seconds  how far into the program the viewer tuned in
--   seconds         seconds watched (capped by the program's runtime and the real time elapsed)
--   ended           tune_away | power_off | program_end; NULL while it is still playing
CREATE TABLE watch_sessions (
    id              INTEGER PRIMARY KEY,
    session         TEXT NOT NULL UNIQUE,
    device          TEXT,
    channel         TEXT NOT NULL,
    item            TEXT NOT NULL,
    kind            TEXT NOT NULL CHECK (kind IN ('episode', 'movie')),
    season          INTEGER,
    episode         INTEGER,
    sim_date        TEXT NOT NULL,
    started_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    offset_seconds  INTEGER NOT NULL DEFAULT 0,
    seconds         INTEGER NOT NULL,
    runtime_seconds INTEGER NOT NULL,
    ended           TEXT CHECK (ended IN ('tune_away', 'power_off', 'program_end'))
);
CREATE INDEX watch_sessions_started ON watch_sessions (started_at);
