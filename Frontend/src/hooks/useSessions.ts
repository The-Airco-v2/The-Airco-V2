import {
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
  type QueryFunctionContext,
} from "@tanstack/react-query";
import { apiFetchJson } from "@/lib/api";
import type {
  EmployeeIntelligenceSnapshotEvent,
  EmployeeIntelligenceUpsertEvent,
  OverviewPatchEvent,
  OverviewSnapshotEvent,
} from "@/lib/live/types";
import type {
  CreateSessionPayload,
  EmployeeIntelligence,
  OverviewToday,
  Session,
  StartSessionPayload,
  UltimateRuntimeStatus,
} from "@/types";

export const overviewTodayQueryKey = ["overview-today"] as const;
export const employeeIntelligenceQueryKey = (sessionId: string | null) =>
  ["employee-intelligence", sessionId] as const;

const queryFreshnessByKey = new Map<string, number>();

const EMPTY_OVERVIEW: OverviewToday = {
  session: null,
  summary: {
    counts: {
      expected: 0,
      present: 0,
      late: 0,
      absent: 0,
      unknown: 0,
      active_exceptions: 0,
    },
    phone: {
      violators: 0,
      total_minutes: 0,
    },
    health: {
      camera_total: 0,
      camera_active: 0,
      entrance_cameras: 0,
      coverage_status: "healthy",
    },
  },
};

type EmployeeIntelligenceResponse =
  | EmployeeIntelligence[]
  | {
      employees?: EmployeeIntelligence[] | null;
    };

function hasOwnProperty<TObject extends object, TKey extends PropertyKey>(
  value: TObject,
  key: TKey,
): value is TObject & Record<TKey, unknown> {
  return Object.prototype.hasOwnProperty.call(value, key);
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

function findEmployeeIndex(
  employees: EmployeeIntelligence[],
  nextEmployee: EmployeeIntelligence,
) {
  if (nextEmployee.employee_id) {
    const matchedById = employees.findIndex(
      (employee) => employee.employee_id === nextEmployee.employee_id,
    );
    if (matchedById !== -1) {
      return matchedById;
    }

    return employees.findIndex(
      (employee) =>
        employee.employee_id === null && employee.employee_name === nextEmployee.employee_name,
    );
  }

  return employees.findIndex(
    (employee) =>
      employee.employee_id === null && employee.employee_name === nextEmployee.employee_name,
  );
}

function mergeOverviewCache(
  current: OverviewToday | undefined,
  event: OverviewSnapshotEvent | OverviewPatchEvent,
): OverviewToday {
  if (event.type === "overview.snapshot") {
    return {
      session: event.payload.session ?? null,
      summary: event.payload.summary ?? current?.summary ?? EMPTY_OVERVIEW.summary,
    };
  }

  return {
    session: hasOwnProperty(event.payload, "session")
      ? ((event.payload.session as OverviewToday["session"] | undefined) ?? null)
      : (current?.session ?? EMPTY_OVERVIEW.session),
    summary: event.payload.summary ?? current?.summary ?? EMPTY_OVERVIEW.summary,
  };
}

export function applyOverviewLiveEvent(
  queryClient: QueryClient,
  event: OverviewSnapshotEvent | OverviewPatchEvent,
) {
  touchQueryFreshness(queryClient, overviewTodayQueryKey);
  queryClient.setQueryData<OverviewToday>(overviewTodayQueryKey, (current) =>
    mergeOverviewCache(current, event),
  );
}

export function applyEmployeeIntelligenceLiveEvent(
  queryClient: QueryClient,
  sessionId: string,
  event: EmployeeIntelligenceSnapshotEvent | EmployeeIntelligenceUpsertEvent,
) {
  const queryKey = employeeIntelligenceQueryKey(sessionId);
  touchQueryFreshness(queryClient, queryKey);
  queryClient.setQueryData<EmployeeIntelligence[]>(queryKey, (current) => {
    if (event.type === "employee_intelligence.snapshot") {
      return event.payload.employees;
    }

    const employees = current ?? [];
    const nextEmployee = event.payload.employee;
    const existingIndex = findEmployeeIndex(employees, nextEmployee);

    if (existingIndex === -1) {
      return [nextEmployee, ...employees];
    }

    const nextEmployees = [...employees];
    nextEmployees[existingIndex] = nextEmployee;
    return nextEmployees;
  });
}

export function createOverviewQueryFn(
  queryClient: QueryClient,
  fetcher: (signal: AbortSignal) => Promise<OverviewToday> = (signal) =>
    apiFetchJson<OverviewToday>("/api/v2/overview/today", { signal }),
) {
  return guardQueryFreshness(queryClient, overviewTodayQueryKey, fetcher);
}

export function createEmployeeIntelligenceQueryFn(
  queryClient: QueryClient,
  sessionId: string,
  fetcher: (signal: AbortSignal) => Promise<EmployeeIntelligenceResponse> = (signal) =>
    apiFetchJson<EmployeeIntelligenceResponse>(
      `/api/v2/sessions/${sessionId}/employee-intelligence`,
      {
        signal,
      },
    ),
) {
  return guardQueryFreshness(queryClient, employeeIntelligenceQueryKey(sessionId), async (signal) => {
    const response = await fetcher(signal);
    if (Array.isArray(response)) {
      return response;
    }
    return Array.isArray(response?.employees) ? response.employees : [];
  });
}

export function useSessions() {
  return useQuery({
    queryKey: ["sessions"],
    queryFn: () => apiFetchJson<Session[]>("/api/v2/sessions"),
  });
}

export function useUltimateRuntimeStatus() {
  return useQuery({
    queryKey: ["ultimate-runtime-status"],
    queryFn: () =>
      apiFetchJson<UltimateRuntimeStatus>("/api/v2/sessions/runtime/ultimate-status"),
    refetchInterval: 5_000,
  });
}

export function useCreateSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: CreateSessionPayload) =>
      apiFetchJson<Session>("/api/v2/sessions", {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["sessions"] }),
  });
}

export function useStartSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, reid_profile }: StartSessionPayload) =>
      apiFetchJson(`/api/v2/sessions/${id}/start`, {
        method: "POST",
        body: JSON.stringify({ reid_profile }),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["sessions"] }),
  });
}

export function useStopSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiFetchJson(`/api/v2/sessions/${id}/stop`, { method: "POST" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["sessions"] }),
  });
}

export function useOverviewToday() {
  const queryClient = useQueryClient();
  return useQuery({
    queryKey: overviewTodayQueryKey,
    queryFn: createOverviewQueryFn(queryClient),
    refetchInterval: 60_000,
  });
}

export function useEmployeeIntelligence(sessionId: string | null) {
  const queryClient = useQueryClient();
  return useQuery({
    queryKey: employeeIntelligenceQueryKey(sessionId),
    queryFn: createEmployeeIntelligenceQueryFn(queryClient, sessionId ?? ""),
    enabled: !!sessionId,
    refetchInterval: 30_000,
  });
}
