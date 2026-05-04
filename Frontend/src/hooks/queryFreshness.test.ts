import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";
import { applyAlertsLiveEvent, alertsQueryKey, createAlertsQueryFn } from "@/hooks/useAlerts";
import {
  applyEmployeeIntelligenceLiveEvent,
  applyOverviewLiveEvent,
  createEmployeeIntelligenceQueryFn,
  createOverviewQueryFn,
  employeeIntelligenceQueryKey,
  overviewTodayQueryKey,
} from "@/hooks/useSessions";
import type { AlertCreatedEvent, EmployeeIntelligenceUpsertEvent, OverviewSnapshotEvent } from "@/lib/live/types";
import type { Alert, EmployeeIntelligence, OverviewToday } from "@/types";

function createQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  });
}

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  const promise = new Promise<T>((innerResolve) => {
    resolve = innerResolve;
  });

  return { promise, resolve };
}

const overviewLive: OverviewToday = {
  session: {
    id: "session-live",
    name: "Live Shift",
    status: "running",
    created_at: "2026-03-31T09:00:00Z",
    started_at: "2026-03-31T09:01:00Z",
    stopped_at: null,
  },
  summary: {
    counts: {
      expected: 12,
      present: 11,
      late: 1,
      absent: 0,
      unknown: 0,
      active_exceptions: 1,
    },
    phone: { violators: 1, total_minutes: 2 },
    health: {
      camera_total: 4,
      camera_active: 4,
      entrance_cameras: 1,
      coverage_status: "healthy",
    },
  },
};

const overviewStale: OverviewToday = {
  session: {
    id: "session-stale",
    name: "Stale Shift",
    status: "paused",
    created_at: "2026-03-31T08:00:00Z",
    started_at: "2026-03-31T08:01:00Z",
    stopped_at: null,
  },
  summary: {
    counts: {
      expected: 8,
      present: 6,
      late: 1,
      absent: 1,
      unknown: 0,
      active_exceptions: 0,
    },
    phone: { violators: 0, total_minutes: 0 },
    health: {
      camera_total: 3,
      camera_active: 2,
      entrance_cameras: 1,
      coverage_status: "degraded",
    },
  },
};

const alice: EmployeeIntelligence = {
  employee_id: "emp-1",
  employee_name: "Alice",
  training_status: "trained",
  presence: { is_present: true, entered_at: "2026-03-31T08:03:00Z", last_seen: "2026-03-31T09:10:00Z" },
  live_status: "working",
  location: { current_zone: "Assembly", current_camera: "cam-1" },
  movement_path: [{ zone: "Assembly", time: "2026-03-31T08:03:00Z" }],
  productivity: { working_seconds: 1800, idle_seconds: 120, productivity_percent: 93 },
  dwell_analysis: { Assembly: 1800 },
  violations: { phone_usage_minutes: 0, phone_violation: false, restricted_zone_violation: false },
  recognition_state: "recognized",
  best_thumbnail_url: null,
  confidence: 0.98,
};

const alertLive: Alert = {
  id: "alert-live",
  type: "phone",
  severity: "high",
  camera_id: "cam-1",
  camera_name: "Entrance",
  session_id: "session-1",
  message: "Phone detected",
  acknowledged: false,
  created_at: "2026-03-31T09:12:00Z",
  evidence_url: null,
  snapshot_url: null,
};

describe("query freshness guards", () => {
  it("keeps a fresher overview live update when a stale refetch resolves afterward", async () => {
    const queryClient = createQueryClient();
    queryClient.setQueryData(overviewTodayQueryKey, overviewStale);

    const fetcher = deferred<OverviewToday>();
    const queryFn = createOverviewQueryFn(queryClient, () => fetcher.promise);
    const queryPromise = queryClient.fetchQuery({
      queryKey: overviewTodayQueryKey,
      queryFn,
    });

    const liveEvent: OverviewSnapshotEvent = {
      type: "overview.snapshot",
      version: "1",
      tenant_id: "tenant-1",
      session_id: "session-live",
      occurred_at: "2026-03-31T09:15:00Z",
      payload: overviewLive,
    };

    applyOverviewLiveEvent(queryClient, liveEvent);
    fetcher.resolve(overviewStale);

    await expect(queryPromise).resolves.toEqual(overviewLive);
    expect(queryClient.getQueryData(overviewTodayQueryKey)).toEqual(overviewLive);
  });

  it("keeps a fresher employee intelligence live update when a stale refetch resolves afterward", async () => {
    const queryClient = createQueryClient();
    const sessionId = "session-1";
    const queryKey = employeeIntelligenceQueryKey(sessionId);
    queryClient.setQueryData(queryKey, [alice]);

    const liveEmployee: EmployeeIntelligence = {
      ...alice,
      live_status: "idle",
      productivity: { working_seconds: 1500, idle_seconds: 500, productivity_percent: 75 },
    };
    const fetcher = deferred<EmployeeIntelligence[]>();
    const queryFn = createEmployeeIntelligenceQueryFn(queryClient, sessionId, () => fetcher.promise);
    const queryPromise = queryClient.fetchQuery({
      queryKey,
      queryFn,
    });

    const liveEvent: EmployeeIntelligenceUpsertEvent = {
      type: "employee_intelligence.upsert",
      version: "1",
      tenant_id: "tenant-1",
      session_id: sessionId,
      occurred_at: "2026-03-31T09:16:00Z",
      payload: { employee: liveEmployee },
    };

    applyEmployeeIntelligenceLiveEvent(queryClient, sessionId, liveEvent);
    fetcher.resolve([alice]);

    await expect(queryPromise).resolves.toEqual([liveEmployee]);
    expect(queryClient.getQueryData(queryKey)).toEqual([liveEmployee]);
  });

  it("normalizes wrapped employee intelligence API responses into an array", async () => {
    const queryClient = createQueryClient();
    const sessionId = "session-1";
    const queryKey = employeeIntelligenceQueryKey(sessionId);

    const queryFn = createEmployeeIntelligenceQueryFn(
      queryClient,
      sessionId,
      async () => ({ employees: [alice] }) as unknown as EmployeeIntelligence[],
    );

    await expect(
      queryClient.fetchQuery({
        queryKey,
        queryFn,
      }),
    ).resolves.toEqual([alice]);

    expect(queryClient.getQueryData(queryKey)).toEqual([alice]);
  });

  it("keeps a fresher alerts live update when a stale refetch resolves afterward", async () => {
    const queryClient = createQueryClient();
    const params = { session_id: "session-1", limit: 1 };
    const queryKey = alertsQueryKey(params);
    queryClient.setQueryData(queryKey, [alertLive]);

    const fetcher = deferred<Alert[]>();
    const queryFn = createAlertsQueryFn(queryClient, params, () => fetcher.promise);
    const queryPromise = queryClient.fetchQuery({
      queryKey,
      queryFn,
    });

    const liveEvent: AlertCreatedEvent = {
      type: "alert.created",
      version: "1",
      tenant_id: "tenant-1",
      session_id: "session-1",
      occurred_at: "2026-03-31T09:17:00Z",
      payload: { ...alertLive, id: "alert-live-2" },
    };

    applyAlertsLiveEvent(queryClient, "session-1", liveEvent);
    fetcher.resolve([alertLive]);

    await expect(queryPromise).resolves.toEqual([liveEvent.payload]);
    expect(queryClient.getQueryData(queryKey)).toEqual([liveEvent.payload]);
  });
});
