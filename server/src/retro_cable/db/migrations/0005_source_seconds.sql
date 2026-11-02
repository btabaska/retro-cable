-- The measured length of a source file, from ffprobe when `ingest scan` (or `normalize`)
-- reads it, rounded down. NULL until measured. The scheduler uses it when there is no
-- normalized file (media_seconds), and checks a normalized file against it: a file shorter
-- than half its expected runtime (a sample or a truncated encode) is never scheduled.
ALTER TABLE episodes ADD COLUMN source_seconds INTEGER;
ALTER TABLE movies ADD COLUMN source_seconds INTEGER;
