-- Catalog of what is on disk, filled by the Plex importer and the TMDB gap filler.
-- Show and movie ids are the catalog slugs from catalog/carriage.yaml.

CREATE TABLE shows (
    id               TEXT PRIMARY KEY,
    title            TEXT NOT NULL,
    year             INTEGER,
    plex_rating_key  TEXT UNIQUE,
    tmdb_id          INTEGER,
    tvdb_id          INTEGER,
    imdb_id          TEXT,
    summary          TEXT,
    updated_at       TEXT
);

-- air_date is the original first US air date as imported (ISO yyyy-mm-dd), before
-- overrides.yaml or synthetic.yaml shift it; the scheduler applies those at read time.
-- An episode with no file_path is known from TMDB but not on disk yet.
CREATE TABLE episodes (
    id               INTEGER PRIMARY KEY,
    show_id          TEXT NOT NULL REFERENCES shows (id) ON DELETE CASCADE,
    season           INTEGER NOT NULL,
    episode          INTEGER NOT NULL,
    title            TEXT NOT NULL DEFAULT '',
    air_date         TEXT,
    air_date_source  TEXT CHECK (air_date_source IN ('plex', 'tmdb')),
    runtime_seconds  INTEGER,
    summary          TEXT,
    plex_rating_key  TEXT UNIQUE,
    file_path        TEXT,
    file_size        INTEGER,
    UNIQUE (show_id, season, episode)
);

CREATE INDEX episodes_by_air_date ON episodes (air_date);

CREATE TABLE movies (
    id               TEXT PRIMARY KEY,
    title            TEXT NOT NULL,
    year             INTEGER,
    premiere_date    TEXT,
    runtime_seconds  INTEGER,
    plex_rating_key  TEXT UNIQUE,
    tmdb_id          INTEGER,
    imdb_id          TEXT,
    summary          TEXT,
    file_path        TEXT,
    file_size        INTEGER,
    updated_at       TEXT
);

CREATE TABLE import_runs (
    id           INTEGER PRIMARY KEY,
    source       TEXT NOT NULL,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    stats        TEXT
);
