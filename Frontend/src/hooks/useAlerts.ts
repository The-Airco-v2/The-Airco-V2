import {
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
  type QueryFunctionContext,
} from "@tanstack/react-query";
import { apiFetchJson } from "@/lib/api";
import type { AlertAcknowledgedEvent, AlertCreatedEvent, AlertUpdatedEvent } from "@/lib/live/types";
import type { Alert } from "@/types";

export interface AlertsQueryParams {
  session_id?: string | null;
  limit?: number;
}

export const alertsQueryKey = (params?: AlertsQueryParams) =>
  ["alerts", params?.session_id ?? null, params?.limit ?? null] as const;

const queryFreshnessByKey = new Map<string, number>();

function isCanonicalAlert(payload: Partial<Alert>): payload is Alert {
  return (
    typeof payload.id === "string" &&
    typeof payload.type === "string" &&
    typeof payload.severity === "string" &&
    typeof payload.session_id === "string" &&
    typeof payload.message === "string" &&
    typeof payload.acknowledged === "boolean" &&
    typeof payload.created_at === "string" &&
    "camera_id" in payload &&
    "camera_name" in payload
  );
}

function queryKeyToFreshnessKey(queryKey: readonly unknown[]) {
  return JSON.stringify(queryKey);
}

function readQueryFreshness(queryKey: readonly unknown[]) {
  return queryFreshnessByKey.get(queryKeyToFreshnessKey(queryKey)) ?? 0;
}

function bumpQueryFreshness(queryKey: readonly unknown[]) {
  const freshnessKey = queryKeyToFreshnessKey(queryKey);
  queryFreshnessByKey.set(freshnessKey, readQueryFreshness(queryKey) + 1);
}

function guardQueryFreshness<T>(
  queryClient: QueryClient,
  queryKey: readonly unknown[],
  fetcher: (signal: AbortSignal) => Promise<T>,
) {
  return async ({ signal }: QueryFunctionContext) => {
    const freshnessAtStart = readQueryFreshness(queryKey);
    const data = await fetcher(signal);

    if (signal.aborted || readQueryFreshness(queryKey) !== freshnessAtStart) {
      return queryClient.getQueryData<T>(queryKey) ?? data;
    }

    return data;
  };
}

function touchQueryFreshness(queryClient: QueryClient, queryKey: readonly unknown[]) {
  bumpQueryFreshness(queryKey);
  void queryClient.cancelQueries({ queryKey });
}

function mergeAlertsCache(
  current: Alert[] | undefined,
  event: AlertCreatedEvent | AlertUpdatedEvent | AlertAcknowledgedEvent,
  limit?: number | null,
) {
  const alerts = current ?? [];
  const incomingId = event.payload.id;
  const nextLimit = limit ?? undefined;

  if (!incomingId) {
    return alerts;
  }

  const existingIndex = alerts.findIndex((alert) => alert.id === incomingId);

  if (event.type === "alert.created") {
    const canonicalAlert = isCanonicalAlert(event.payload) ? event.payload : null;
    if (!canonicalAlert) {
      return alerts;
    }

    const rest = existingIndex === -1 ? alerts : alerts.filter((alert) => alert.id !== incomingId);
    const nextAlerts = [existingIndex === -1 ? canonicalAlert : { ...alerts[existingIndex], ...canonicalAlert }, ...rest];
    return typeof nextLimit === "number" ? nextAlerts.slice(0, nextLimit) : nextAlerts;
  }

  if (existingIndex !== -1) {
    const nextAlerts = [...alerts];
    nextAlerts[existingIndex] = { ...alerts[existingIndex], ...event.payload };
    return nextAlerts;
  }

  const canonicalAlert = isCanonicalAlert(event.payload) ? event.payload : null;
  if (!canonicalAlert) {
    return alerts;
  }

  const nextAlerts = [canonicalAlert, ...alerts];
  return typeof nextLimit === "number" ? nextAlerts.slice(0, nextLimit) : nextAlerts;
}

export function applyAlertsLiveEvent(
  queryClient: QueryClient,
  sessionId: string,
  event: AlertCreatedEvent | AlertUpdatedEvent | AlertAcknowledgedEvent,
) {
  const matchingQueries = queryClient
    .getQueryCache()
    .findAll({ queryKey: ["alerts", sessionId] });

  if (matchingQueries.length === 0) {
    return;
  }

  for (const query of matchingQueries) {
    const queryKey = query.queryKey as ReturnType<typeof alertsQueryKey>;
    const [, , limit] = queryKey;
    touchQueryFreshness(queryClient, queryKey);
    queryClient.setQueryData<Alert[]>(query.queryKey, (current) =>
      mergeAlertsCache(current, event, limit),
    );
  }
}

export function createAlertsQueryFn(
  queryClient: QueryClient,
  params?: AlertsQueryParams,
  fetcher: (signal: AbortSignal) => Promise<Alert[]> = (signal) => {
    const sessionId = params?.session_id ?? null;
    const search = new URLSearchParams();
    if (sessionId) {
      search.set("session_id", sessionId);
    }
    if (params?.limit) {
      search.set("limit", String(params.limit));
    }
    const qs = search.toString() ? `?${search}` : "";

    return apiFetchJson<Alert[]>(`/api/v2/alerts${qs}`, { signal });
  },
) {
  return guardQueryFreshness(queryClient, alertsQueryKey(params), fetcher);
}

export function useAlerts(params?: AlertsQueryParams) {
  const sessionId = params?.session_id ?? null;
  const queryClient = useQueryClient();

  return useQuery({
    queryKey: alertsQueryKey(params),
    queryFn: createAlertsQueryFn(queryClient, params),
    enabled: !!sessionId,
  });
}

export function useAcknowledgeAlert() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiFetchJson(`/api/v2/alerts/${id}/acknowledge`, { method: "POST" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["alerts"] }),
  });
}
