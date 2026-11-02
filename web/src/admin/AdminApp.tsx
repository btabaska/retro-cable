import "./admin.css";
import { useCallback, useState } from "react";
import { type AdminApi, type AdminChannelList, type AdminState, liveAdminApi } from "./api";
import { ChannelEditor } from "./ChannelEditor";
import { ChannelList } from "./ChannelList";
import { Toast } from "./Toast";

type View = { kind: "list" } | { kind: "edit"; id: string | null };

interface ToastState {
  message: string;
  canUndo: boolean;
  key: number;
}

interface AdminAppProps {
  api?: AdminApi;
}

/** /admin on a phone: channels you can add, edit, preview and undo. LAN only, no sign-in. */
export function AdminApp({ api = liveAdminApi }: AdminAppProps) {
  const [view, setView] = useState<View>({ kind: "list" });
  const [list, setList] = useState<AdminChannelList | null>(null);
  const [toast, setToast] = useState<ToastState | null>(null);
  const [version, setVersion] = useState(0);

  const changed = useCallback((message: string, state: AdminState) => {
    setToast({ message, canUndo: state.can_undo, key: Date.now() });
    setVersion((n) => n + 1);
    setView({ kind: "list" });
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

  return (
    <div className="adm" data-testid="admin">
      {view.kind === "edit" ? (
        <ChannelEditor
          key={view.id ?? "new"}
          api={api}
          channelId={view.id}
          list={list}
          onBack={() => {
            setView({ kind: "list" });
          }}
          onChanged={changed}
        />
      ) : (
        <div className="adm-screen">
          <header className="adm-header">
            <h1 className="adm-title">Retro Cable</h1>
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
      )}
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
      {/* M1 shows only Channels; Holidays, Manager and Build arrive with their phases. */}
      <nav className="adm-tabs" aria-label="Admin sections">
        <span className="adm-tab adm-tab-on" aria-current="page">
          Channels
        </span>
      </nav>
    </div>
  );
}
