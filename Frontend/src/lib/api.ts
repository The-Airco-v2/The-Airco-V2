import type { ApiErrorField, ApiErrorResponse } from "@/types";

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public fields: ApiErrorField[] = [],
  ) {
    super(message);
  }
}

/**
 * Resolve the absolute URL for an API request.
 *
 * - In dev, VITE_API_URL is unset and the Vite proxy in vite.config.ts
 *   maps /api/... → http://localhost:8000.
 * - In production builds (Cloudflare Pages), VITE_API_URL is set to
 *   the deployed API origin (e.g. https://api.the-airco.net) so the
 *   browser hits it directly cross-origin.
 *
 * Absolute URLs (http://, https://, ws://, wss://) pass through.
 */
export function resolveApiUrl(path: string): string {
  if (/^[a-z]+:\/\//i.test(path)) return path;
  const base = (import.meta as ImportMeta & {
    env?: Record<string, string | undefined>;
  }).env?.VITE_API_URL;
  if (typeof base !== "string" || base.length === 0) return path;
  const trimmedBase = base.replace(/\/$/, "");
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  return `${trimmedBase}${normalizedPath}`;
}

export async function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  const hasBody = init?.body != null;
  const res = await fetch(resolveApiUrl(path), {
    ...init,
    credentials: "include",
    headers: {
      ...(hasBody ? { "Content-Type": "application/json" } : {}),
      ...init?.headers,
    },
  });

  if (res.status === 401) {
    window.dispatchEvent(new Event("auth:expired"));
  }

  return res;
}

function parseApiError(body: ApiErrorResponse | null, statusText: string) {
  if (body?.detail && typeof body.detail === "object") {
    return {
      code: typeof body.detail.code === "string" ? body.detail.code : "unknown_error",
      message: typeof body.detail.message === "string" ? body.detail.message : statusText,
      fields: Array.isArray(body.detail.fields) ? body.detail.fields : [],
    };
  }

  if (typeof body?.detail === "string") {
    return {
      code: "unknown_error",
      message: body.detail,
      fields: [],
    };
  }

  return {
    code: body?.error ?? "unknown_error",
    message: body?.message ?? statusText,
    fields: [],
  };
}

export async function apiFetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await apiFetch(path, init);
  const text = await res.text();
  const body = (() => {
    if (!text) {
      return null;
    }
    try {
      return JSON.parse(text) as ApiErrorResponse;
    } catch {
      return null;
    }
  })();

  if (!res.ok) {
    const parsed = parseApiError(body, res.statusText);
    throw new ApiError(res.status, parsed.code, parsed.message, parsed.fields);
  }

  return (body ?? undefined) as T;
}
