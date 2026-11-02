import { useState } from "react";
import { PREMIERE_MAX, PREMIERE_MIN, runLine } from "./draft";

interface PremiereControlProps {
  index: number;
  /** The pretend premiere in force on this channel, if any. */
  value: string | null;
  /** Set in catalog/synthetic.yaml: shown, but edited in the files, not here. */
  locked: boolean;
  /** The show's first and last dates on this channel, as the server computed them. */
  run: { first: string; last: string | null } | null;
  /** Save (or with null, clear) and resolve to an error message, or null when saved. */
  onSave: (value: string | null) => Promise<string | null>;
}

/** A pretend premiere on a YAML channel's show row: the channel is read-only, but a show's
 * premiere on it is an input /admin can change. It saves straight away (undo from the toast). */
export function PremiereControl({ index, value, locked, run, onSave }: PremiereControlProps) {
  const [text, setText] = useState(value ?? "");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const id = `premiere-${index}`;

  const submit = async (next: string | null) => {
    setBusy(true);
    setError(null);
    const problem = await onSave(next);
    setBusy(false);
    if (problem) setError(problem);
    else if (next === null) setText("");
  };

  return (
    <div className="adm-premiere-static">
      {value && run && (
        <span className="adm-hint" data-testid={`premiere-run-${index}`}>
          {runLine(run)}
        </span>
      )}
      {locked ? (
        <span className="adm-hint">This pretend premiere is set in the catalog files.</span>
      ) : (
        <>
          <label className="adm-label" htmlFor={id}>
            Pretend premiere
          </label>
          <div className="adm-premiere">
            <input
              id={id}
              type="date"
              className="adm-input"
              min={PREMIERE_MIN}
              max={PREMIERE_MAX}
              value={text}
              aria-invalid={error ? true : undefined}
              aria-describedby={error ? `err-${id}` : undefined}
              onChange={(e) => {
                setText(e.target.value);
                setError(null);
              }}
            />
            <button
              type="button"
              className="adm-btn adm-btn-outline"
              disabled={busy || text === "" || text === (value ?? "")}
              onClick={() => {
                void submit(text);
              }}
            >
              Set
            </button>
            {value && (
              <button
                type="button"
                className="adm-btn adm-btn-text"
                disabled={busy}
                onClick={() => {
                  void submit(null);
                }}
              >
                Clear
              </button>
            )}
          </div>
          {error && (
            <span id={`err-${id}`} className="adm-field-error" role="alert">
              {error}
            </span>
          )}
        </>
      )}
    </div>
  );
}
