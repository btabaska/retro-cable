import "./admin.css";
import { useCallback, useEffect, useRef, useState } from "react";
import { type AdminApi, type AdminChannelList, type AdminState, liveAdminApi } from "./api";
import { ChannelEditor } from "./ChannelEditor";
import { ChannelList } from "./ChannelList";
import { focusTitle } from "./focus";
import { MediaPanel } from "./MediaPanel";
import { Toast } from "./Toast";

type View = { kind: "list" } | { kind: "edit"; id: string | null } | { kind: "media" };

interface ToastState {
  message: string;
  canUndo: boolean;
  key: number;
}

interface AdminAppProps {
  api?: AdminApi;
}

/** /admin on a phone: channels you can add, edit, preview and undo, and the media conversion's
 * progress. LAN only, no sign-in. */
export function AdminApp({ api = liveAdminApi }: AdminAppProps) {
  const [view, setView] = useState<View>({ kind: "list" });
  const [list, setList] = useState<AdminChannelList | null>(null);
  const [toast, setToast] = useState<ToastState | null>(null);
  const [version, setVersion] = useState(0);
  const first = useRef(true);

  // After a save, delete, discard or tab change, focus lands on the new screen's title.
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    focusTitle();
  }, [view]);

  const changed = useCallback((message: string, state: AdminState) => {
    setToast({ message, canUndo: state.can_undo, key: Date.now() });
    setVersion((n) => n + 1);
    setView({ kind: "list" });
  }, []);

  const premiere = useCallback((message: string, state: AdminState) => {
    setToast({ message, canUndo: state.can_undo, key: Date.now() });
    setVersion((n) => n + 1);
  }, []);

  const undo = async () => {
    try {
      const result = await api.undo();
      setToast({ message: result.message, canUndo: false, key: Date.now() });
    } catch {
      setToast({ message: "Nothing to undo", canUndo: false, key: Date.now() });
    }
    setVersion((n) => n + 1);
    setView({ kind: "list" });
  };

  const dismiss = useCallback(() => {
    setToast(null);
  }, []);

  let body;
  if (view.kind === "edit") {
    body = (
      <ChannelEditor
        key={view.id ?? "new"}
        api={api}
        channelId={view.id}
        list={list}
        onBack={() => {
          setView({ kind: "list" });
        }}
        onChanged={changed}
        onPremiere={premiere}
      />
    );
  } else if (view.kind === "media") {
    body = (
      <div className="adm-screen">
        <header className="adm-header">
          <h1 className="adm-title" tabIndex={-1}>
            Media
          </h1>
        </header>
        <main className="adm-body">
          <MediaPanel api={api} />
        </main>
      </div>
    );
  } else {
    body = (
      <div className="adm-screen">
        <header className="adm-header">
          <h1 className="adm-title" tabIndex={-1}>
            Retro Cable
          </h1>
          <a className="adm-header-link" href="/">
            Watch TV
          </a>
        </header>
        <main className="adm-body">
          <ChannelList
            api={api}
            version={version}
            onOpen={(id, l) => {
              setList(l);
              setView({ kind: "edit", id });
            }}
          />
        </main>
      </div>
    );
  }

  const onMedia = view.kind === "media";
  return (
    <div className="adm" data-testid="admin">
      {body}
      {toast && (
        <Toast
          key={toast.key}
          message={toast.message}
          canUndo={toast.canUndo}
          onUndo={() => {
            void undo();
          }}
          onDone={dismiss}
        />
      )}
      {/* Holidays, Manager and Build arrive with their phases. */}
      <nav className="adm-tabs" aria-label="Admin sections">
        <button
          type="button"
          className={`adm-tab adm-tab-button${onMedia ? "" : " adm-tab-on"}`}
          aria-current={onMedia ? undefined : "page"}
          onClick={() => {
            if (onMedia) setView({ kind: "list" });
          }}
        >
          Channels
        </button>
        <button
          type="button"
          className={`adm-tab adm-tab-button${onMedia ? " adm-tab-on" : ""}`}
          aria-current={onMedia ? "page" : undefined}
          onClick={() => {
            if (!onMedia) setView({ kind: "media" });
          }}
        >
          Media
        </button>
      </nav>
    </div>
  );
}
