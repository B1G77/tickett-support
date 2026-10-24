import { lazy, Suspense, type ReactNode } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { useMe } from "./auth";
import { ToastProvider } from "./components/Toast";
import Shell from "./layout/Shell";
import AutoSend from "./pages/AutoSend";
import Drafts from "./pages/Drafts";
import Health from "./pages/Health";
import Login from "./pages/Login";

// the charts library is only for the admin: its own file, loaded when Painel opens
const Overview = lazy(() => import("./pages/Overview"));

function AdminOnly({ children }: { children: ReactNode }) {
  const { me } = useMe();
  return me?.role === "admin" ? children : <Navigate to="/" replace />;
}

export default function App() {
  const { me, checked } = useMe();
  if (!checked) return <div className="splash" aria-busy="true" />;
  if (!me) return <Login />;
  return (
    <ToastProvider>
      <Routes>
        <Route element={<Shell />}>
          <Route index element={<Drafts />} />
          <Route path="painel" element={<AdminOnly><Suspense fallback={<div className="page" aria-busy="true" />}><Overview /></Suspense></AdminOnly>} />
          <Route path="estado" element={<AdminOnly><Health /></AdminOnly>} />
          <Route path="envio-automatico" element={<AdminOnly><AutoSend /></AdminOnly>} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </ToastProvider>
  );
}
