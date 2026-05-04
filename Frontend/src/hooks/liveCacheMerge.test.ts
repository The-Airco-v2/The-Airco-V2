import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";
import { applyAlertsLiveEvent, alertsQueryKey } from "@/hooks/useAlerts";
import {
  applyEmployeeIntelligenceLiveEvent,
  applyOverviewLiveEvent,
  employeeIntelligenceQueryKey,
  overviewTodayQueryKey,
} from "@/hooks/useSessions";
import type {
  AlertAcknowledgedEvent,
  AlertCreatedEvent,
  EmployeeIntelligenceSnapshotEvent,
  EmployeeIntelligenceUpsertEvent,
  OverviewPatchEvent,
  OverviewSnapshotEvent,
} from "@/lib/live/types";
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

const overviewBase: OverviewToday = {
  session: {
    id: "session-1",
    name: "Morning Shift",
    status: "running",
    created_at: "2026-03-31T08:00:00Z",
    started_at: "2026-03-31T08:01:00Z",
    stopped_at: null,
  },
  summary: {
    counts: {
      expected: 20,
      present: 18,
      late: 1,
      absent: 1,
      unknown: 0,
      active_exceptions: 2,
    },
    phone: { violators: 1, total_minutes: 4 },
    health: {
      camera_total: 6,
      camera_active: 6,
      entrance_cameras: 2,
      coverage_status: "healthy",
    },
  },
};

const alice: EmployeeIntelligence = {
  employee_id: "emp-1",
  employee_name: "Alice",
  training_status: "trained",
  presence: { is_present: true, entered_at: "2026-03-31T08:03:00Z", last_seen: "2026-03-31T08:10:00Z" },
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

const bob: EmployeeIntelligence = {
  ...alice,
  employee_id: "emp-2",
  employee_name: "Bob",
  live_status: "idle",
  productivity: { working_seconds: 900, idle_seconds: 600, productivity_percent: 60 },
};

const alertOne: Alert = {
  id: "alert-1",
  type: "phone",
  severity: "high",
  camera_id: "cam-1",
  camera_name: "Entrance",
  session_id: "session-1",
  message: "Phone detected",
  acknowledged: false,
  created_at: "2026-03-31T08:11:00Z",
  evidence_url: null,
  snapshot_url: null,
};

const alertTwo: Alert = {
  ...alertOne,
  id: "alert-2",
  type: "absence",
  severity: "medium",
  camera_id: "cam-2",
  camera_name: "Assembly",
  message: "Worker missing",
  created_at: "2026-03-31T08:12:00Z",
};

describe("live cache merge behavior", () => {
  it("replaces and merges overview cache from live events", () => {
    const queryClient = createQueryClient();
    queryClient.setQueryData(overviewTodayQueryKey, overviewBase);

    const snapshot: OverviewSnapshotEvent = {
      type: "overview.snapshot",
      version: "1",
      tenant_id: "tenant-1",
      session_id: "session-2",
      occurred_at: "2026-03-31T09:00:00Z",
      payload: {
        session: {
          id: "session-2",
          name: "Late Shift",
          status: "paused",
          created_at: "2026-03-31T09:00:00Z",
          started_at: null,
          stopped_at: null,
        },
        summary: {
          counts: {
            expected: 10,
            present: 7,
            late: 2,
            absent: 1,
            unknown: 0,
            active_exceptions: 3,
          },
          phone: { violators: 2, total_minutes: 8 },
          health: {
            camera_total: 4,
            camera_active: 3,
            entrance_cameras: 1,
            coverage_status: "degraded",
          },
        },
      },
    };

    applyOverviewLiveEvent(queryClient, snapshot);
    expect(queryClient.getQueryData(overviewTodayQueryKey)).toEqual({
      session: snapshot.payload.session,
      summary: snapshot.payload.summary,
    });

    const patch: OverviewPatchEvent = {
      type: "overview.patch",
      version: "1",
      tenant_id: "tenant-1",
      session_id: "session-2",
      occurred_at: "2026-03-31T09:05:00Z",
      payload: {
        session: {
          id: "session-2",
          name: "Late Shift",
          status: "running",
          created_at: "2026-03-31T09:00:00Z",
          started_at: "2026-03-31T09:04:00Z",
          stopped_at: null,
        },
      },
    };

    applyOverviewLiveEvent(queryClient, patch);
    expect(queryClient.getQueryData(overviewTodayQueryKey)).toEqual({
      session: patch.payload.session,
      summary: snapshot.payload.summary,
    });
  });

  it("clears the cached session when overview.patch explicitly sets session to null", () => {
    const queryClient = createQueryClient();
    queryClient.setQueryData(overviewTodayQueryKey, overviewBase);

    const patch: OverviewPatchEvent = {
      type: "overview.patch",
      version: "1",
      tenant_id: "tenant-1",
      session_id: null,
      occurred_at: "2026-03-31T09:10:00Z",
      payload: {
        session: null,
      },
    };

    applyOverviewLiveEvent(queryClient, patch);
    expect(queryClient.getQueryData(overviewTodayQueryKey)).toEqual({
      session: null,
      summary: overviewBase.summary,
    });
  });

  it("replaces and upserts employee intelligence rows by identity", () => {
    const queryClient = createQueryClient();
    queryClient.setQueryData(employeeIntelligenceQueryKey("session-1"), [alice]);

    const snapshot: EmployeeIntelligenceSnapshotEvent = {
      type: "employee_intelligence.snapshot",
      version: "1",
      tenant_id: "tenant-1",
      session_id: "session-1",
      occurred_at: "2026-03-31T08:15:00Z",
      payload: {
        employees: [alice, bob],
      },
    };

    applyEmployeeIntelligenceLiveEvent(queryClient, "session-1", snapshot);
    expect(queryClient.getQueryData(employeeIntelligenceQueryKey("session-1"))).toEqual([alice, bob]);

    const upsert: EmployeeIntelligenceUpsertEvent = {
      type: "employee_intelligence.upsert",
      version: "1",
      tenant_id: "tenant-1",
      session_id: "session-1",
      occurred_at: "2026-03-31T08:20:00Z",
      payload: {
        employee: {
          ...bob,
          live_status: "working",
          productivity: { working_seconds: 1200, idle_seconds: 300, productivity_percent: 80 },
        },
      },
    };

    applyEmployeeIntelligenceLiveEvent(queryClient, "session-1", upsert);
    expect(queryClient.getQueryData(employeeIntelligenceQueryKey("session-1"))).toEqual([
      alice,
      upsert.payload.employee,
    ]);
  });

  it("reconciles recognition upgrades from null employee_id to a real employee_id", () => {
    const queryClient = createQueryClient();
    const unidentifiedAlice: EmployeeIntelligence = {
      ...alice,
      employee_id: null,
      recognition_state: "unknown",
      confidence: 0.41,
    };

    queryClient.setQueryData(employeeIntelligenceQueryKey("session-1"), [unidentifiedAlice]);

    const upsert: EmployeeIntelligenceUpsertEvent = {
      type: "employee_intelligence.upsert",
      version: "1",
      tenant_id: "tenant-1",
      session_id: "session-1",
      occurred_at: "2026-03-31T08:25:00Z",
      payload: {
        employee: {
          ...alice,
          recognition_state: "recognized",
          confidence: 0.97,
        },
      },
    };

    applyEmployeeIntelligenceLiveEvent(queryClient, "session-1", upsert);
    expect(queryClient.getQueryData(employeeIntelligenceQueryKey("session-1"))).toEqual([
      upsert.payload.employee,
    ]);
  });

  it("updates all alerts caches for a session using deterministic keys and respects per-query limits", () => {
    const queryClient = createQueryClient();
    queryClient.setQueryData(alertsQueryKey({ session_id: "session-1" }), [alertOne]);
    queryClient.setQueryData(alertsQueryKey({ session_id: "session-1", limit: 1 }), [alertOne]);

    const created: AlertCreatedEvent = {
      type: "alert.created",
      version: "1",
      tenant_id: "tenant-1",
      session_id: "session-1",
      occurred_at: "2026-03-31T08:13:00Z",
      payload: { ...alertTwo },
    };

    applyAlertsLiveEvent(queryClient, "session-1", created);
    expect(queryClient.getQueryData(alertsQueryKey({ session_id: "session-1" }))).toEqual([
      alertTwo,
      alertOne,
    ]);
    expect(queryClient.getQueryData(alertsQueryKey({ session_id: "session-1", limit: 1 }))).toEqual([
      alertTwo,
    ]);

    const acknowledged: AlertAcknowledgedEvent = {
      type: "alert.acknowledged",
      version: "1",
      tenant_id: "tenant-1",
      session_id: "session-1",
      occurred_at: "2026-03-31T08:14:00Z",
      payload: {
        id: "alert-2",
        acknowledged: true,
      },
    };

    applyAlertsLiveEvent(queryClient, "session-1", acknowledged);
    expect(queryClient.getQueryData(alertsQueryKey({ session_id: "session-1" }))).toEqual([
      { ...alertTwo, acknowledged: true },
      alertOne,
    ]);
    expect(queryClient.getQueryData(alertsQueryKey({ session_id: "session-1", limit: 1 }))).toEqual([
      { ...alertTwo, acknowledged: true },
    ]);
  });
});
