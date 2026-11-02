-- Normalized media written by `python -m retro_cable.ingest normalize`.
--   media_path             the normalized MP4, relative to the media root (RC_MEDIA_DIR)
--   media_seconds          its duration from ffprobe, rounded down; the scheduler's runtime
--   media_source_size      size of the source file when it was normalized
--   media_source_mtime_ns  mtime of the source file when it was normalized
-- A changed source size or mtime, or a missing output, makes the job encode the item again.
ALTER TABLE episodes ADD COLUMN media_path TEXT;
ALTER TABLE episodes ADD COLUMN media_seconds INTEGER;
ALTER TABLE episodes ADD COLUMN media_source_size INTEGER;
ALTER TABLE episodes ADD COLUMN media_source_mtime_ns INTEGER;

ALTER TABLE movies ADD COLUMN media_path TEXT;
ALTER TABLE movies ADD COLUMN media_seconds INTEGER;
ALTER TABLE movies ADD COLUMN media_source_size INTEGER;
ALTER TABLE movies ADD COLUMN media_source_mtime_ns INTEGER;

ALTER TABLE bumpers ADD COLUMN media_path TEXT;
ALTER TABLE bumpers ADD COLUMN media_seconds INTEGER;
ALTER TABLE bumpers ADD COLUMN media_source_size INTEGER;
ALTER TABLE bumpers ADD COLUMN media_source_mtime_ns INTEGER;
