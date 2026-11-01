import { type KeyboardEvent, useEffect, useRef, useState } from "react";
import type { ServerVersion } from "./api/client";
import { moveFocus } from "./lib/focus";
import { Stage } from "./Stage";

const CELLS = ["Placeholder One", "Placeholder Two", "Placeholder Three"];

interface AppProps {
  loadVersion: () => Promise<ServerVersion>;
}

export function App({ loadVersion }: AppProps) {
  const [focused, setFocused] = useState(0);
  const [version, setVersion] = useState<string>("Tuning…");
  const cellRefs = useRef<(HTMLDivElement | null)[]>([]);

  useEffect(() => {
    cellRefs.current[focused]?.focus();
  }, [focused]);

  useEffect(() => {
    let live = true;
    loadVersion().then(
      (v) => {
        if (live) setVersion(`Server v${v.version} · ${v.commit}`);
      },
      () => {
        if (live) setVersion("No signal");
      },
    );
    return () => {
      live = false;
    };
  }, [loadVersion]);

  const onKeyDown = (event: KeyboardEvent) => {
    const next = moveFocus({ row: 0, col: focused }, event.key, { rows: 1, cols: CELLS.length });
    if (next === null) return;
    event.preventDefault();
    setFocused(next.col);
  };

  return (
    <Stage>
      <header className="header">
        <div className="wordmark">RETRO CABLE</div>
        <div className="server-version" data-testid="server-version">
          {version}
        </div>
      </header>
      <main className="hello">
        <h1>Hello, Mountain View</h1>
        <div className="row" role="grid" aria-label="Guide preview" onKeyDown={onKeyDown}>
          <div className="channel">
            <span className="number">00</span>
            <span className="call-sign">TEST</span>
          </div>
          <div className="cells" role="row">
            {CELLS.map((title, i) => (
              <div
                key={title}
                ref={(el) => {
                  cellRefs.current[i] = el;
                }}
                role="gridcell"
                aria-selected={i === focused}
                tabIndex={i === focused ? 0 : -1}
                className="cell"
                onFocus={() => {
                  setFocused(i);
                }}
              >
                <span className="time">{`8:${String(i * 20).padStart(2, "0")}`}</span>
                <span className="title">{title}</span>
              </div>
            ))}
          </div>
        </div>
      </main>
    </Stage>
  );
}
