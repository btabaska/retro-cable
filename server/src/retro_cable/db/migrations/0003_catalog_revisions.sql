-- Admin edits (Phase 10): an append-only history of catalog overlays. The row with the highest
-- id is the head, the active overlay. Saving appends a row whose parent is the head; undo
-- appends a row that copies the parent's overlay (and the parent's parent), so history is
-- never rewritten. `overlay` is canonical JSON (retro_cable.catalog.overlay).
CREATE TABLE catalog_revisions (
    id          INTEGER PRIMARY KEY,
    created_at  TEXT NOT NULL,
    parent_id   INTEGER REFERENCES catalog_revisions (id),
    overlay     TEXT NOT NULL,
    note        TEXT NOT NULL DEFAULT ''
);
