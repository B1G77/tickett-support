import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { request, setOnUnauthorized } from "./api";

export type Me = { user: string; role: "admin" | "agent" };

type Auth = {
  me: Me | null;
  checked: boolean; // /api/me has answered once
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
};

const AuthContext = createContext<Auth | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [checked, setChecked] = useState(false);

  useEffect(() => {
    setOnUnauthorized(() => setMe(null));
    request<Me>("/api/me")
      .then(setMe)
      .catch(() => setMe(null))
      .finally(() => setChecked(true));
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    setMe(await request<Me>("/api/login", { method: "POST", body: { username, password } }));
  }, []);

  const logout = useCallback(async () => {
    try {
      await request("/api/logout", { method: "POST", body: {} });
    } finally {
      setMe(null);
    }
  }, []);

  const value = useMemo(() => ({ me, checked, login, logout }), [me, checked, login, logout]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useMe(): Auth {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useMe outside AuthProvider");
  return ctx;
}
