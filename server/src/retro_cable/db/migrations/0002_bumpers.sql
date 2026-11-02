-- Bumpers and promos played between programs, from _bumpers/<channel>/<era>/ on the media root.
CREATE TABLE bumpers (
    id               INTEGER PRIMARY KEY,
    channel_id       TEXT NOT NULL,
    file_path        TEXT NOT NULL,
    runtime_seconds  INTEGER NOT NULL CHECK (runtime_seconds > 0),
    UNIQUE (channel_id, file_path)
);

CREATE INDEX bumpers_by_channel ON bumpers (channel_id);
