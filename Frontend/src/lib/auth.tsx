import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useReducer,
  type ReactNode,
} from "react";
import { useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "./api";
import type { AuthStatus, AuthUser, BackendAuthResponse } from "@/types";

interface AuthState {
  status: AuthStatus;
  user: AuthUser | null;
}

type AuthAction =
  | { type: "SET_LOADING" }
  | { type: "SET_RESULT"; payload: { status: AuthStatus; user: AuthUser | null } };

function authReducer(state: AuthState, action: AuthAction): AuthState {
  switch (action.type) {
    case "SET_LOADING":
      return { ...state, status: "loading" };
    case "SET_RESULT":
      return { status: action.payload.status, user: action.payload.user };
    default:
      return state;
  }
}

function backendAuthStatus(data: BackendAuthResponse): AuthStatus {
  switch (data.accountState) {
    case "authenticated":
      return "authenticated";
    case "not_provisioned":
      return "not_provisioned";
    case "inactive_user":
      return "inactive";
    case "inactive_tenant":
      return "tenant_inactive";
    default:
      return "unauthenticated";
  }
}

function backendAuthUser(data: BackendAuthResponse): AuthUser | null {
  if (
    data.accountState !== "authenticated" ||
    !data.userId ||
    !data.email ||
    !data.tenantId ||
    !data.role
  ) {
    return null;
  }

  return {
    id: data.userId,
    email: data.email,
    name: data.email.split("@")[0] || data.email,
    role: data.role,
    tenant_id: data.tenantId,
  };
}

interface AuthContextValue {
  user: AuthUser | null;
  status: AuthStatus;
  refresh: () => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [state, dispatch] = useReducer(authReducer, {
    status: "loading",
    user: null,
  });

  const fetchMe = useCallback(async () => {
    dispatch({ type: "SET_LOADING" });
    try {
      const res = await apiFetch("/api/auth/me");
      if (res.status === 401) {
        dispatch({ type: "SET_RESULT", payload: { status: "unauthenticated", user: null } });
        return;
      }
      const data: BackendAuthResponse = await res.json();
      dispatch({
        type: "SET_RESULT",
        payload: {
          status: backendAuthStatus(data),
          user: backendAuthUser(data),
        },
      });
    } catch {
      dispatch({ type: "SET_RESULT", payload: { status: "unauthenticated", user: null } });
    }
  }, []);

  useEffect(() => {
    fetchMe();
  }, [fetchMe]);

  useEffect(() => {
    const handler = () => {
      dispatch({ type: "SET_RESULT", payload: { status: "unauthenticated", user: null } });
    };
    window.addEventListener("auth:expired", handler);
    return () => window.removeEventListener("auth:expired", handler);
  }, []);

  const refresh = useCallback(async () => {
    await fetchMe();
  }, [fetchMe]);

  const logout = useCallback(async () => {
    await apiFetch("/api/auth/logout", { method: "POST" });
    queryClient.clear();
    dispatch({ type: "SET_RESULT", payload: { status: "unauthenticated", user: null } });
  }, [queryClient]);

  return (
    <AuthContext.Provider value={{ user: state.user, status: state.status, refresh, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth must be used within AuthProvider");
  }
  return ctx;
}
