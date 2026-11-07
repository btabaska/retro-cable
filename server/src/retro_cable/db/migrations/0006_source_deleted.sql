-- When the rig deleted a source file after a whole, checked encode of it (M4: originals of
-- Live TV downloads, only under RC_DELETE_SOURCES_UNDER). The row then has no file_path but
-- keeps its encode (media_path, media_seconds, media_source_*) and source_seconds: it stays in
-- the library and the manifest, and normalize counts it as done instead of encoding it again.
-- NULL while the row has a source, or never had one deleted. A scan that finds a new source
-- for the row clears it.
ALTER TABLE episodes ADD COLUMN source_deleted_at TEXT;
ALTER TABLE movies ADD COLUMN source_deleted_at TEXT;
