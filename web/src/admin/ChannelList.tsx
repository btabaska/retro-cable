import { useEffect, useState } from "react";
import type { AdminApi, AdminChannelList } from "./api";
import { channelMeta } from "./draft";

interface ChannelListProps {
  api: AdminApi;
  /** Bump to reload after a save, delete or undo. */
  version: number;
  onOpen: (id: string | null, list: AdminChannelList) => void;
}

/** AdminChannels: every channel by number. Each row opens its editor. */
export function ChannelList({ api, version, onOpen }: ChannelListProps) {
  const [data, setData] = useState<AdminChannelList | null>(null);
  const [failed, setFailed] = useState(false);
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    let live = true;
    api.listChannels().then(
      (list) => {
        if (!live) return;
        setData(list);
        setFailed(false);
      },
      () => {
        if (live) setFailed(true);
      },
    );
    return () => {
      live = false;
    };
  }, [api, version, retry]);

  const newButton = (list: AdminChannelList) => (
    <button
      type="button"
      className="adm-btn adm-btn-gold"
      onClick={() => {
        onOpen(null, list);
      }}
    >
      + New channel
    </button>
  );

  if (failed && !data) {
    return (
      <div className="adm-card adm-error-card" role="alert">
        <p>Couldn&apos;t load channels.</p>
        <button
          type="button"
          className="adm-btn adm-btn-outline"
          onClick={() => {
            setRetry((n) => n + 1);
          }}
        >
          Retry
        </button>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="adm-rows" aria-busy="true" aria-label="Loading channels">
        {Array.from({ length: 6 }, (_, i) => (
          <div key={i} className="adm-row adm-skeleton" />
        ))}
      </div>
    );
  }

  if (data.channels.length === 0) {
    return (
      <div className="adm-empty">
        <p>No channels yet</p>
        {newButton(data)}
      </div>
    );
  }

  return (
    <>
      <div className="adm-section-head">
        <h2 className="adm-h2">Channels</h2>
        {newButton(data)}
      </div>
      <ul className="adm-rows" data-testid="admin-channels">
        {data.channels.map((c) => (
          <li key={c.id}>
            <button
              type="button"
              className={`adm-row adm-row-link${c.custom ? " adm-row-custom" : ""}`}
              data-testid={`admin-channel-${c.id}`}
              onClick={() => {
                onOpen(c.id, data);
              }}
            >
              <span className="adm-row-number">{c.number}</span>
              <span className="adm-row-main">
                <span className="adm-row-title">{c.name}</span>
                <span className="adm-row-meta">
                  {c.custom ? "Custom · " : ""}
                  {channelMeta(c)}
                </span>
              </span>
              <span className="adm-chevron" aria-hidden="true">
                ›
              </span>
            </button>
          </li>
        ))}
      </ul>
    </>
  );
}
