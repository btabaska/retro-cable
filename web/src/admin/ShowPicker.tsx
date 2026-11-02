import { useEffect, useState } from "react";
import type { AdminApi, LibraryEntry } from "./api";
import { carriedOn, groupLibrary, yearsLine } from "./draft";
import { Sheet } from "./Sheet";

interface ShowPickerProps {
  api: AdminApi;
  /** Ids already on this channel: shown checked and disabled. */
  chosen: Set<string>;
  onAdd: (entry: LibraryEntry) => void;
  onClose: () => void;
}

/** "+ Add show": search the whole library, including shows no channel carries. */
export function ShowPicker({ api, chosen, onAdd, onClose }: ShowPickerProps) {
  const [q, setQ] = useState("");
  const [items, setItems] = useState<LibraryEntry[] | null>(null);
  const [everything, setEverything] = useState<number | null>(null);

  useEffect(() => {
    let live = true;
    api.searchLibrary(q).then(
      (found) => {
        if (!live) return;
        setItems(found);
        if (q === "") setEverything(found.length);
      },
      () => {
        if (live) setItems([]);
      },
    );
    return () => {
      live = false;
    };
  }, [api, q]);

  const groups = groupLibrary(items ?? []);
  const row = (e: LibraryEntry) => {
    const on = chosen.has(e.id);
    return (
      <li key={e.id} className="adm-row">
        <span className="adm-row-main">
          <span className="adm-row-title">{e.title}</span>
          <span className="adm-row-meta">
            {yearsLine(e)}
            {e.channels.length > 0 && ` · ${carriedOn(e)}`}
          </span>
        </span>
        <button
          type="button"
          className="adm-icon-btn"
          aria-label={on ? `${e.title} is on this channel` : `Add ${e.title}`}
          disabled={on}
          onClick={() => {
            onAdd(e);
          }}
        >
          {on ? "✓" : "+"}
        </button>
      </li>
    );
  };

  return (
    <Sheet title="Add a show" onClose={onClose}>
      <div className="adm-sheet-head">
        <label className="adm-visually-hidden" htmlFor="show-search">
          Search the library
        </label>
        <input
          id="show-search"
          className="adm-input"
          type="search"
          placeholder="Search the library"
          autoFocus
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
          }}
        />
        <button type="button" className="adm-btn adm-btn-text" onClick={onClose}>
          Done
        </button>
      </div>
      <div className="adm-sheet-body" data-testid="show-picker">
        {items === null ? (
          <div className="adm-rows" aria-busy="true">
            <div className="adm-row adm-skeleton" />
            <div className="adm-row adm-skeleton" />
          </div>
        ) : everything === 0 ? (
          <p className="adm-empty">Your library is empty. Run an import first.</p>
        ) : items.length === 0 ? (
          <p className="adm-empty">No shows match &lsquo;{q}&rsquo;</p>
        ) : (
          <>
            {groups.free.length > 0 && (
              <>
                <h3 className="adm-h3">Not on any channel</h3>
                <ul className="adm-rows">{groups.free.map(row)}</ul>
              </>
            )}
            {groups.carried.length > 0 && (
              <>
                <h3 className="adm-h3">On other channels</h3>
                <ul className="adm-rows">{groups.carried.map(row)}</ul>
              </>
            )}
          </>
        )}
      </div>
    </Sheet>
  );
}
