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

export async function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  const hasBody = init?.body != null;
  const res = await fetch(path, {
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
