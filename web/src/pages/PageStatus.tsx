// Shared by Rascunhos and Por Responder: the toolbar with the last refresh, and the loading/error states.
import type { ReactNode } from "react";
import type { AppState } from "../types";
import { updatedAt } from "./useAppState";

export function Toolbar({ data, error, children }: { data: AppState | null; error: string; children?: ReactNode }) {
  return (
    <div className="toolbar">
      <div className="toolbar-main">{children}</div>
      {data && (
        <span className={"updated" + (error ? " updated-error" : "")}>
          {error ? `Sem atualização: ${error}` : updatedAt(data.generated_at)}
        </span>
      )}
    </div>
  );
}

/** Skeleton cards on the first load, a retry box when it failed, else the page. */
export function PageStatus({ data, error, reload, children }:
  { data: AppState | null; error: string; reload: () => Promise<void>; children: ReactNode }) {
  if (data) return <>{children}</>;
  if (error) {
    return (
      <div className="empty" role="alert">
        <p className="empty-title">Não foi possível carregar.</p>
        <p className="muted">{error}</p>
        <button type="button" className="btn" onClick={() => void reload()}>Tentar outra vez</button>
      </div>
    );
  }
  return (
    <div className="card-list" aria-busy="true" aria-label="A carregar">
      <div className="card skeleton" />
      <div className="card skeleton" />
    </div>
  );
}
