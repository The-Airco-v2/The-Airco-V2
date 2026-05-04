import { afterEach, describe, expect, it, vi } from "vitest";

type Cleanup = (() => void) | void;
type SubscriptionMock = {
  mock: {
    calls: Array<[string, (event: unknown) => void]>;
    results: Array<{ value: ReturnType<typeof vi.fn> }>;
  };
};

async function loadOverviewHook(tenantId: string | null) {
  const queryClient = { scope: "overview" };
  const applyOverviewLiveEvent = vi.fn();
  const subscribeToTenantOverview = vi.fn(() => vi.fn());
  const cleanups: Cleanup[] = [];

  vi.doMock("react", () => ({
    useEffect: (effect: () => Cleanup) => {
      cleanups.push(effect());
    },
  }));
  vi.doMock("@tanstack/react-query", () => ({
    useQueryClient: () => queryClient,
  }));
  vi.doMock("@/lib/live/subscribe", () => ({
    subscribeToTenantOverview,
  }));
  vi.doMock("@/hooks/useSessions", () => ({
    applyOverviewLiveEvent,
  }));

  const { useLiveOverview } = await import("@/hooks/useLiveOverview");
  useLiveOverview(tenantId);

  return {
    queryClient,
    applyOverviewLiveEvent,
    subscribeToTenantOverview,
    cleanup: cleanups[0],
  };
}

async function loadEmployeeHook(sessionId: string | null) {
  const queryClient = { scope: "employee" };
  const applyEmployeeIntelligenceLiveEvent = vi.fn();
  const subscribeToSession = vi.fn(() => vi.fn());
  const cleanups: Cleanup[] = [];

  vi.doMock("react", () => ({
    useEffect: (effect: () => Cleanup) => {
      cleanups.push(effect());
    },
  }));
  vi.doMock("@tanstack/react-query", () => ({
    useQueryClient: () => queryClient,
  }));
  vi.doMock("@/lib/live/subscribe", () => ({
    subscribeToSession,
  }));
  vi.doMock("@/hooks/useSessions", () => ({
    applyEmployeeIntelligenceLiveEvent,
  }));

  const { useLiveEmployeeIntelligence } = await import("@/hooks/useLiveEmployeeIntelligence");
  useLiveEmployeeIntelligence(sessionId);

  return {
    queryClient,
    applyEmployeeIntelligenceLiveEvent,
    subscribeToSession,
    cleanup: cleanups[0],
  };
}

async function loadAlertsHook(sessionId: string | null) {
  const queryClient = { scope: "alerts" };
  const applyAlertsLiveEvent = vi.fn();
  const subscribeToAlerts = vi.fn(() => vi.fn());
  const cleanups: Cleanup[] = [];

  vi.doMock("react", () => ({
    useEffect: (effect: () => Cleanup) => {
      cleanups.push(effect());
    },
  }));
  vi.doMock("@tanstack/react-query", () => ({
    useQueryClient: () => queryClient,
  }));
  vi.doMock("@/lib/live/subscribe", () => ({
    subscribeToAlerts,
  }));
  vi.doMock("@/hooks/useAlerts", () => ({
    applyAlertsLiveEvent,
  }));

  const { useLiveAlerts } = await import("@/hooks/useLiveAlerts");
  useLiveAlerts(sessionId);

  return {
    queryClient,
    applyAlertsLiveEvent,
    subscribeToAlerts,
    cleanup: cleanups[0],
  };
}

afterEach(() => {
  vi.resetModules();
  vi.clearAllMocks();
  vi.unmock("react");
  vi.unmock("@tanstack/react-query");
  vi.unmock("@/lib/live/subscribe");
  vi.unmock("@/hooks/useSessions");
  vi.unmock("@/hooks/useAlerts");
});

describe("live hook subscriptions", () => {
  it("subscribes overview updates and forwards events into the cache patcher", async () => {
    const { queryClient, applyOverviewLiveEvent, subscribeToTenantOverview, cleanup } =
      await loadOverviewHook("tenant-1");
    const event = { type: "overview.patch", payload: { session: null } };
    const subscribeMock = subscribeToTenantOverview as unknown as SubscriptionMock;

    expect(subscribeToTenantOverview).toHaveBeenCalledTimes(1);
    expect(subscribeToTenantOverview).toHaveBeenCalledWith("tenant-1", expect.any(Function));

    const listener = subscribeMock.mock.calls[0]?.[1];
    expect(listener).toBeTypeOf("function");
    listener?.(event);
    expect(applyOverviewLiveEvent).toHaveBeenCalledWith(queryClient, event);

    expect(typeof cleanup).toBe("function");
    cleanup?.();
    const unsubscribe = subscribeMock.mock.results[0]?.value;
    expect(unsubscribe).toHaveBeenCalledTimes(1);
  });

  it("subscribes employee intelligence updates and forwards events into the cache patcher", async () => {
    const { queryClient, applyEmployeeIntelligenceLiveEvent, subscribeToSession, cleanup } =
      await loadEmployeeHook("session-1");
    const event = { type: "employee_intelligence.upsert", payload: { employee: { employee_id: "emp-1" } } };
    const subscribeMock = subscribeToSession as unknown as SubscriptionMock;

    expect(subscribeToSession).toHaveBeenCalledTimes(1);
    expect(subscribeToSession).toHaveBeenCalledWith("session-1", expect.any(Function));

    const listener = subscribeMock.mock.calls[0]?.[1];
    expect(listener).toBeTypeOf("function");
    listener?.(event);
    expect(applyEmployeeIntelligenceLiveEvent).toHaveBeenCalledWith(queryClient, "session-1", event);

    expect(typeof cleanup).toBe("function");
    cleanup?.();
    const unsubscribe = subscribeMock.mock.results[0]?.value;
    expect(unsubscribe).toHaveBeenCalledTimes(1);
  });

  it("subscribes alert updates and forwards events into the cache patcher", async () => {
    const { queryClient, applyAlertsLiveEvent, subscribeToAlerts, cleanup } =
      await loadAlertsHook("session-1");
    const event = { type: "alert.created", payload: { id: "alert-1" } };
    const subscribeMock = subscribeToAlerts as unknown as SubscriptionMock;

    expect(subscribeToAlerts).toHaveBeenCalledTimes(1);
    expect(subscribeToAlerts).toHaveBeenCalledWith("session-1", expect.any(Function));

    const listener = subscribeMock.mock.calls[0]?.[1];
    expect(listener).toBeTypeOf("function");
    listener?.(event);
    expect(applyAlertsLiveEvent).toHaveBeenCalledWith(queryClient, "session-1", event);

    expect(typeof cleanup).toBe("function");
    cleanup?.();
    const unsubscribe = subscribeMock.mock.results[0]?.value;
    expect(unsubscribe).toHaveBeenCalledTimes(1);
  });

  it("does not subscribe when the live hook input is missing", async () => {
    const overview = await loadOverviewHook(null);
    expect(overview.subscribeToTenantOverview).not.toHaveBeenCalled();

    const employee = await loadEmployeeHook(null);
    expect(employee.subscribeToSession).not.toHaveBeenCalled();

    const alerts = await loadAlertsHook(null);
    expect(alerts.subscribeToAlerts).not.toHaveBeenCalled();
  });
});
