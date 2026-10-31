// Who is logged in, for the whole app. Login, logout and "who am I".
import { useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import {
  api,
  ApiError,
  forgetTokens,
  hasRefreshToken,
  refreshTokens,
  saveTokens,
  setLoggedOutHandler,
  type Tokens,
} from "./api";
import type { Me } from "./types";

type LoginResult = "ok" | "need-code";

interface AuthState {
  me: Me | null;
  /** true while we check a saved login at start-up */
  loading: boolean;
  login(email: string, password: string, code?: string): Promise<LoginResult>;
  logout(everywhere?: boolean): Promise<void>;
  /** Read /me again (after changing the name or two-factor login). */
  reload(): Promise<void>;
  /** Log in with tokens we already have (after accepting an invitation). */
  start(tokens: Tokens): Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(hasRefreshToken());
  const queries = useQueryClient();

  const reload = useCallback(async () => setMe(await api<Me>("/me")), []);

  useEffect(() => {
    setLoggedOutHandler(() => {
      setMe(null);
      queries.clear();
    });
    if (!hasRefreshToken()) return;
    refreshTokens()
      .then((ok) => (ok ? reload() : undefined))
      .catch(() => forgetTokens())
      .finally(() => setLoading(false));
  }, [queries, reload]);

  const start = useCallback(
    async (tokens: Tokens) => {
      saveTokens(tokens);
      await reload();
    },
    [reload],
  );

  const login = useCallback(
    async (email: string, password: string, code?: string): Promise<LoginResult> => {
      try {
        const tokens = await api<Tokens>("/auth/login", {
          method: "POST",
          auth: false,
          body: { email, password, ...(code ? { code } : {}) },
        });
        await start(tokens);
        return "ok";
      } catch (err) {
        // The password was right; two-factor login is on (FR-70).
        if (err instanceof ApiError && err.status === 401 && !code &&
            err.message === "Two-factor code required") return "need-code";
        throw err;
      }
    },
    [start],
  );

  const logout = useCallback(
    async (everywhere = false) => {
      try {
        await api(everywhere ? "/auth/logout-all" : "/auth/logout", { method: "POST" });
      } catch {
        // logged out anyway, below
      }
      forgetTokens();
      setMe(null);
      queries.clear();
    },
    [queries],
  );

  return (
    <AuthContext.Provider value={{ me, loading, login, logout, reload, start }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  const state = useContext(AuthContext);
  if (!state) throw new Error("useAuth must be used inside <AuthProvider>");
  return state;
}

/** The logged-in user (only use on pages that need a login). */
export function useMe(): Me {
  const { me } = useAuth();
  if (!me) throw new Error("Not logged in");
  return me;
}
