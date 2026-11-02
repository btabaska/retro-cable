import { useEffect, useMemo, useRef, useState } from "react";
import type { AdminApi, AdminChannelList, AdminState, ChannelDraft, CheckIssue, DraftShow } from "./api";
import {
  carriageLine,
  emptyDraft,
  FIRST_YEAR,
  issuesFor,
  LAST_YEAR,
  nextFreeNumber,
  readyToSave,
  sameDraft,
} from "./draft";
import { Sheet } from "./Sheet";
import { ShowPicker } from "./ShowPicker";
import { WeekPreview } from "./WeekPreview";

interface ChannelEditorProps {
  api: AdminApi;
  /** null for a new channel. */
  channelId: string | null;
  list: AdminChannelList | null;
  onBack: () => void;
  onChanged: (message: string, state: AdminState) => void;
}

type Sheets = "none" | "picker" | "preview" | "delete" | "discard";

const DEFAULT_PREVIEW_YEAR = 2000;

/** AdminChannel: edit a custom channel, or view a YAML channel read-only. */
export function ChannelEditor({ api, channelId, list, onBack, onChanged }: ChannelEditorProps) {
  const [loaded, setLoaded] = useState<ChannelDraft | null>(channelId ? null : emptyDraft());
  const [draft, setDraft] = useState<ChannelDraft>(emptyDraft());
  const [custom, setCustom] = useState(channelId === null);
  const [titles, setTitles] = useState<Record<string, string>>({});
  const [errors, setErrors] = useState<CheckIssue[]>([]);
  const [warnings, setWarnings] = useState<CheckIssue[]>([]);
  const [checking, setChecking] = useState(false);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [open, setOpen] = useState<number | null>(null);
  const [sheet, setSheet] = useState<Sheets>("none");
  const summary = useRef<HTMLDivElement>(null);
  const readOnly = !custom;
  const placeholder = useMemo(() => (list ? nextFreeNumber(list.channels) : 2), [list]);

  useEffect(() => {
    if (!channelId) return;
    let live = true;
    api.getChannel(channelId).then(
      (c) => {
        if (!live) return;
        setLoaded(c.draft);
        setDraft(c.draft);
        setCustom(c.custom);
      },
      () => {
        if (live) setFailed(true);
      },
    );
    return () => {
      live = false;
    };
  }, [api, channelId, attempt]);

  useEffect(() => {
    let live = true;
    api.searchLibrary("").then(
      (items) => {
        if (live) setTitles(Object.fromEntries(items.map((i) => [i.id, i.title])));
      },
      () => undefined,
    );
    return () => {
      live = false;
    };
  }, [api, channelId]);

  useEffect(() => {
    if (errors.length) summary.current?.focus();
  }, [errors]);

  const dirty = loaded !== null && !sameDraft(loaded, draft);
  const shows = draft.shows ?? [];
  const set = (patch: Partial<ChannelDraft>) => {
    setDraft((d) => ({ ...d, ...patch }));
  };
  const setShow = (i: number, patch: Partial<DraftShow>) => {
    set({ shows: shows.map((s, j) => (j === i ? { ...s, ...patch } : s)) });
  };
  const year = (value: string): number | null => (value === "" ? null : Number(value));

  const save = async () => {
    setChecking(true);
    try {
      const outcome = await api.saveChannel({ draft, channel_id: channelId, year: DEFAULT_PREVIEW_YEAR }, channelId);
      if (outcome.saved) {
        setErrors([]);
        setLoaded(draft);
        onChanged(outcome.result.message, outcome.result.state);
      } else {
        setErrors(outcome.check.errors);
        setWarnings(outcome.check.warnings);
      }
    } catch {
      setErrors([{ field: null, message: "Couldn't reach the server. Nothing was saved." }]);
    } finally {
      setChecking(false);
    }
  };

  const remove = async () => {
    if (!channelId) return;
    setSheet("none");
    try {
      const result = await api.deleteChannel(channelId);
      onChanged(result.message, result.state);
    } catch {
      setErrors([{ field: null, message: "Couldn't delete the channel." }]);
    }
  };

  const back = () => {
    if (dirty) setSheet("discard");
    else onBack();
  };

  const fieldError = (field: string) => {
    const found = issuesFor(errors, field);
    if (!found.length) return null;
    return (
      <span id={`err-${field}`} className="adm-field-error">
        {found.map((e) => e.message).join(" ")}
      </span>
    );
  };
  const invalid = (field: string) => (issuesFor(errors, field).length ? { "aria-invalid": true as const, "aria-describedby": `err-${field}` } : {});

  const focusField = (field: string | null) => {
    if (!field) return;
    const id = field.startsWith("shows.") ? `show-${field.slice(6)}` : `f-${field}`;
    document.getElementById(id)?.focus();
  };

  // An existing channel is named from the list row until it loads, never a placeholder.
  const listed = channelId ? list?.channels.find((c) => c.id === channelId) : undefined;
  const title = channelId ? draft.name || loaded?.name || listed?.name || "" : "New channel";
  const header = (
    <header className="adm-header">
      <button type="button" className="adm-back" onClick={back}>
        ‹ Channels
      </button>
      <h1 className="adm-title">
        {title || <span className="adm-skeleton adm-skeleton-title" aria-label="Loading channel name" />}
      </h1>
    </header>
  );

  if (loaded === null) {
    return (
      <div className="adm-screen">
        {header}
        <main className="adm-body">
          {failed ? (
            <div className="adm-card adm-error-card" role="alert">
              <p>Couldn&apos;t load this channel.</p>
              <button
                type="button"
                className="adm-btn adm-btn-outline"
                onClick={() => {
                  setFailed(false);
                  setAttempt((n) => n + 1);
                }}
              >
                Retry
              </button>
            </div>
          ) : (
            <div aria-busy="true" aria-label="Loading channel" data-testid="editor-loading">
              <div className="adm-card adm-fields">
                {Array.from({ length: 4 }, (_, i) => (
                  <div key={i} className="adm-field">
                    <span className="adm-skeleton adm-skeleton-label" />
                    <span className="adm-skeleton adm-skeleton-input" />
                  </div>
                ))}
              </div>
              <div className="adm-rows">
                {Array.from({ length: 3 }, (_, i) => (
                  <div key={i} className="adm-row adm-skeleton" />
                ))}
              </div>
            </div>
          )}
        </main>
      </div>
    );
  }

  return (
    <div className="adm-screen">
      {header}

      <main className="adm-body adm-body-footer">
        {errors.length > 0 && (
          <div className="adm-card adm-error-card" role="alert" tabIndex={-1} ref={summary} data-testid="check-errors">
            <h2 className="adm-h3">
              Can&apos;t save: {errors.length} {errors.length === 1 ? "problem" : "problems"}
            </h2>
            <ul>
              {errors.map((e) => (
                <li key={`${e.field ?? ""}${e.message}`}>
                  {e.field ? (
                    <a
                      href={`#${e.field}`}
                      onClick={(ev) => {
                        ev.preventDefault();
                        focusField(e.field);
                      }}
                    >
                      {e.message}
                    </a>
                  ) : (
                    e.message
                  )}
                </li>
              ))}
            </ul>
          </div>
        )}
        {warnings.length > 0 && errors.length === 0 && (
          <div className="adm-card adm-warn-card">
            {warnings.map((w) => (
              <p key={w.message}>{w.message}</p>
            ))}
          </div>
        )}
        {readOnly && (
          <p className="adm-note">This channel comes from the catalog files, so it is read-only here.</p>
        )}

        <fieldset className="adm-card adm-fields" disabled={readOnly}>
          <legend className="adm-visually-hidden">Channel</legend>
          <div className="adm-field">
            <label className="adm-label" htmlFor="f-number">Number</label>
            <input
              id="f-number"
              className="adm-input"
              inputMode="numeric"
              placeholder={String(placeholder)}
              value={draft.number ? String(draft.number) : ""}
              onChange={(e) => {
                set({ number: Number(e.target.value.replace(/\D/g, "")) || 0 });
              }}
              {...invalid("number")}
            />
            {fieldError("number")}
          </div>
          <div className="adm-field">
            <label className="adm-label" htmlFor="f-call_sign">Call sign</label>
            <input
              id="f-call_sign"
              className="adm-input"
              maxLength={6}
              autoCapitalize="characters"
              value={draft.call_sign}
              onChange={(e) => {
                set({ call_sign: e.target.value.toUpperCase() });
              }}
              {...invalid("call_sign")}
            />
            {fieldError("call_sign")}
          </div>
          <div className="adm-field adm-field-wide">
            <label className="adm-label" htmlFor="f-name">Name</label>
            <input
              id="f-name"
              className="adm-input"
              value={draft.name}
              onChange={(e) => {
                set({ name: e.target.value });
              }}
              {...invalid("name")}
            />
            {fieldError("name")}
          </div>
          <label className="adm-field">
            <span className="adm-label">Type</span>
            <select
              id="f-type"
              className="adm-select"
              value={draft.type}
              onChange={(e) => {
                set({ type: e.target.value as ChannelDraft["type"] });
              }}
            >
              <option value="network">Network</option>
              <option value="marathon">Marathon</option>
              <option value="movie">Movies</option>
            </select>
          </label>
          <div className="adm-field">
            <label className="adm-label" htmlFor="f-launch_year">On the dial from</label>
            <input
              id="f-launch_year"
              className="adm-input"
              inputMode="numeric"
              placeholder={draft.library ? "—" : String(FIRST_YEAR)}
              disabled={draft.library}
              value={draft.library ? "" : (draft.launch_year?.toString() ?? "")}
              onChange={(e) => {
                set({ launch_year: year(e.target.value.replace(/\D/g, "")) });
              }}
              {...invalid("launch_year")}
            />
            {fieldError("launch_year")}
          </div>
          <label className="adm-field adm-field-wide adm-switch-row">
            <input
              id="f-library"
              type="checkbox"
              role="switch"
              className="adm-switch"
              checked={draft.library}
              onChange={(e) => {
                set({ library: e.target.checked });
              }}
            />
            <span>
              <span className="adm-label-strong">Library</span>
              <span className="adm-hint">{draft.library ? "Available in every year" : "On the dial from its launch year"}</span>
            </span>
          </label>
        </fieldset>

        <div className="adm-section-head">
          <h2 className="adm-h2" id="shows">
            Shows on this channel
          </h2>
          {!readOnly && (
            <button
              type="button"
              className="adm-btn adm-btn-outline"
              onClick={() => {
                setSheet("picker");
              }}
            >
              + Add show
            </button>
          )}
        </div>
        {fieldError("shows")}
        {shows.length === 0 && <p className="adm-empty">No shows yet. Add one from your library.</p>}
        <ul className="adm-rows" data-testid="editor-shows">
          {shows.map((s, i) => {
            const field = `shows.${i}`;
            const expanded = open === i;
            return (
              <li key={s.show} className={`adm-show${issuesFor(errors, field).length ? " adm-show-bad" : ""}`}>
                <button
                  id={`show-${i}`}
                  type="button"
                  className="adm-row adm-row-link"
                  aria-expanded={expanded}
                  onClick={() => {
                    setOpen(expanded ? null : i);
                  }}
                  {...invalid(field)}
                >
                  <span className="adm-row-main">
                    <span className="adm-row-title">{titles[s.show] ?? s.show}</span>
                    <span className="adm-row-meta">
                      {s.kind === "movie" ? "Movie · " : ""}
                      {carriageLine(s)}
                    </span>
                  </span>
                  <span className="adm-chevron" aria-hidden="true">
                    {expanded ? "⌃" : "⌄"}
                  </span>
                </button>
                {fieldError(field)}
                {expanded && !readOnly && (
                  <div className="adm-show-edit">
                    <label className="adm-field">
                      <span className="adm-label">From year</span>
                      <input
                        className="adm-input"
                        inputMode="numeric"
                        placeholder="Launch"
                        value={s.from_year?.toString() ?? ""}
                        onChange={(e) => {
                          setShow(i, { from_year: year(e.target.value.replace(/\D/g, "")) });
                        }}
                      />
                    </label>
                    <label className="adm-field">
                      <span className="adm-label">Until year</span>
                      <input
                        className="adm-input"
                        inputMode="numeric"
                        placeholder={String(LAST_YEAR)}
                        value={s.until_year?.toString() ?? ""}
                        onChange={(e) => {
                          setShow(i, { until_year: year(e.target.value.replace(/\D/g, "")) });
                        }}
                      />
                    </label>
                    <button
                      type="button"
                      className="adm-btn adm-btn-danger-text"
                      onClick={() => {
                        set({ shows: shows.filter((_, j) => j !== i) });
                        setOpen(null);
                      }}
                    >
                      Remove from channel
                    </button>
                  </div>
                )}
              </li>
            );
          })}
        </ul>

        {channelId && custom && (
          <button
            type="button"
            className="adm-btn adm-btn-danger-text adm-delete"
            onClick={() => {
              setSheet("delete");
            }}
          >
            Delete channel
          </button>
        )}
      </main>

      {!readOnly && (
        <footer className="adm-footer">
          <button
            type="button"
            className="adm-btn adm-btn-outline"
            disabled={!readyToSave(draft)}
            onClick={() => {
              setSheet("preview");
            }}
          >
            Preview week
          </button>
          <button
            type="button"
            className="adm-btn adm-btn-gold"
            disabled={!dirty || !readyToSave(draft) || checking}
            onClick={() => {
              void save();
            }}
          >
            {checking ? "Checking…" : "Save"}
          </button>
        </footer>
      )}

      {sheet === "picker" && (
        <ShowPicker
          api={api}
          chosen={new Set(shows.map((s) => s.show))}
          onAdd={(e) => {
            setTitles((t) => ({ ...t, [e.id]: e.title }));
            set({ shows: [...shows, { show: e.id, kind: e.kind }] });
          }}
          onClose={() => {
            setSheet("none");
          }}
        />
      )}
      {sheet === "preview" && (
        <WeekPreview
          api={api}
          draft={draft}
          channelId={channelId}
          initialYear={draft.library ? DEFAULT_PREVIEW_YEAR : Math.max(draft.launch_year ?? DEFAULT_PREVIEW_YEAR, DEFAULT_PREVIEW_YEAR)}
          onClose={() => {
            setSheet("none");
          }}
        />
      )}
      {sheet === "delete" && (
        <Sheet
          title="Delete channel"
          compact
          onClose={() => {
            setSheet("none");
          }}
        >
          <p className="adm-dialog-text">
            Delete {draft.number} {draft.name}? You can undo for 10 seconds.
          </p>
          <div className="adm-dialog-actions">
            <button
              type="button"
              className="adm-btn adm-btn-outline"
              onClick={() => {
                setSheet("none");
              }}
            >
              Cancel
            </button>
            <button
              type="button"
              className="adm-btn adm-btn-danger"
              onClick={() => {
                void remove();
              }}
            >
              Delete
            </button>
          </div>
        </Sheet>
      )}
      {sheet === "discard" && (
        <Sheet
          title="Discard changes"
          compact
          onClose={() => {
            setSheet("none");
          }}
        >
          <p className="adm-dialog-text">Discard changes?</p>
          <div className="adm-dialog-actions">
            <button
              type="button"
              className="adm-btn adm-btn-outline"
              onClick={() => {
                setSheet("none");
              }}
            >
              Keep editing
            </button>
            <button type="button" className="adm-btn adm-btn-danger" onClick={onBack}>
              Discard
            </button>
          </div>
        </Sheet>
      )}
    </div>
  );
}
