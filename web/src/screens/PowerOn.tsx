import { useEffect, useState } from "react";
import type { ServerVersion } from "../api/client";
import { useKeys } from "../hooks/useKeys";
import { yearLabel } from "../lib/years";

interface PowerOnProps {
  year: number;
  loadVersion: () => Promise<ServerVersion>;
  onPower: () => void;
}

/** The TV is off until a key or click, which also lets the browser play sound. */
export function PowerOn({ year, loadVersion, onPower }: PowerOnProps) {
  const [version, setVersion] = useState("Tuning…");

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

  useKeys((event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onPower();
    }
  });

  return (
    <div className="power" data-testid="power-on" onClick={onPower}>
      <div className="power-wordmark">RETRO CABLE</div>
      <div className="power-sub">Mountain View, CA · {yearLabel(year)}</div>
      <button type="button" className="btn btn-gold power-button" autoFocus>
        Press ENTER to turn on the TV
      </button>
      <div className="server-version" data-testid="server-version">
        {version}
      </div>
    </div>
  );
}
