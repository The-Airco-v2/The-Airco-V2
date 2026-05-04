import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  createLiveClient,
  getLiveClient,
  resetLiveClientForTests,
  resolveLiveUrl,
} from "@/lib/live/client";
import { alertsChannel, sessionChannel, tenantOverviewChannel } from "@/lib/live/channels";

describe("live client", () => {
  const originalWindow = globalThis.window;

  beforeEach(() => {
    resetLiveClientForTests();
  });

  afterEach(() => {
    globalThis.window = originalWindow;
  });

  it("returns a shared singleton instance", () => {
    const first = getLiveClient();
    const second = getLiveClient();

    expect(first).toBe(second);
  });

  it("fetches a token lazily when the transport connects", async () => {
    const fetchToken = vi.fn(async () => "token-1");
    const getTokenHolders: Array<() => Promise<string>> = [];

    const client = createLiveClient({
      fetchToken,
      createTransport: ({ getToken }) => {
        getTokenHolders.push(getToken);
        return {
          connect: () => {
            void getToken();
          },
          disconnect: vi.fn(),
          on: vi.fn().mockReturnThis(),
          newSubscription: vi.fn(),
        };
      },
    });

    expect(fetchToken).not.toHaveBeenCalled();

    client.connect();
    await Promise.resolve();

    expect(getTokenHolders).toHaveLength(1);
    expect(fetchToken).toHaveBeenCalledTimes(1);
  });

  it("reuses one subscription per channel and unsubscribes only after the last listener leaves", () => {
    const subscribe = vi.fn();
    const unsubscribe = vi.fn();
    const removeAllListeners = vi.fn();
    const connect = vi.fn();
    const disconnect = vi.fn();
    const publicationHandlers: Array<(ctx: { data: unknown }) => void> = [];
    const subscription = {
      subscribe,
      unsubscribe,
      removeAllListeners,
      on: vi.fn((event: string, handler: (ctx: { data: unknown }) => void) => {
        if (event === "publication") {
          publicationHandlers.push(handler);
        }
        return subscription;
      }),
    };
    const newSubscription = vi.fn(() => subscription);

    const client = createLiveClient({
      fetchToken: async () => "token-1",
      createTransport: () => ({
        connect,
        disconnect,
        on: vi.fn().mockReturnThis(),
        newSubscription,
      }),
    });

    const firstListener = vi.fn();
    const secondListener = vi.fn();

    const disposeFirst = client.subscribe("sessions:session-9", firstListener);
    const disposeSecond = client.subscribe("sessions:session-9", secondListener);

    expect(newSubscription).toHaveBeenCalledTimes(1);
    expect(subscribe).toHaveBeenCalledTimes(1);

    publicationHandlers[0]?.({
      data: {
        type: "employee_intelligence.upsert",
        version: "1",
        tenant_id: null,
        session_id: "session-9",
        occurred_at: null,
        payload: {},
      },
    });

    expect(firstListener).toHaveBeenCalledWith({
      type: "employee_intelligence.upsert",
      version: "1",
      tenant_id: null,
      session_id: "session-9",
      occurred_at: null,
      payload: {},
    });
    expect(secondListener).toHaveBeenCalledWith({
      type: "employee_intelligence.upsert",
      version: "1",
      tenant_id: null,
      session_id: "session-9",
      occurred_at: null,
      payload: {},
    });

    disposeFirst();
    expect(unsubscribe).not.toHaveBeenCalled();

    disposeSecond();
    expect(unsubscribe).toHaveBeenCalledTimes(1);
    expect(removeAllListeners).not.toHaveBeenCalled();
    expect(disconnect).toHaveBeenCalledTimes(1);

    publicationHandlers[0]?.({ data: { type: "employee_intelligence.upsert", version: "1", tenant_id: null, session_id: "session-9", occurred_at: null, payload: {} } });
    expect(firstListener).toHaveBeenCalledTimes(1);
    expect(secondListener).toHaveBeenCalledTimes(1);
    expect(connect).toHaveBeenCalledTimes(2);
  });

  it("reuses the existing transport subscription after an unsubscribe-resubscribe cycle", () => {
    const subscribe = vi.fn();
    const unsubscribe = vi.fn();
    const connect = vi.fn();
    const disconnect = vi.fn();
    const subscription = {
      subscribe,
      unsubscribe,
      on: vi.fn().mockReturnThis(),
    };
    const newSubscription = vi.fn(() => subscription);

    const client = createLiveClient({
      fetchToken: async () => "token-1",
      createTransport: () => ({
        connect,
        disconnect,
        on: vi.fn().mockReturnThis(),
        newSubscription,
      }),
    });

    const firstDispose = client.subscribe("tenant:tenant-1:overview", vi.fn());
    firstDispose();
    const secondDispose = client.subscribe("tenant:tenant-1:overview", vi.fn());
    secondDispose();

    expect(newSubscription).toHaveBeenCalledTimes(1);
    expect(subscribe).toHaveBeenCalledTimes(2);
    expect(unsubscribe).toHaveBeenCalledTimes(2);
  });

  it("ignores malformed publications and wrong-channel event families", () => {
    const publicationHandlers: Array<(ctx: { data: unknown }) => void> = [];
    const subscription = {
      subscribe: vi.fn(),
      unsubscribe: vi.fn(),
      removeAllListeners: vi.fn(),
      on: vi.fn((event: string, handler: (ctx: { data: unknown }) => void) => {
        if (event === "publication") {
          publicationHandlers.push(handler);
        }
        return subscription;
      }),
    };

    const client = createLiveClient({
      fetchToken: async () => "token-1",
      createTransport: () => ({
        connect: vi.fn(),
        disconnect: vi.fn(),
        on: vi.fn().mockReturnThis(),
        newSubscription: vi.fn(() => subscription),
      }),
    });

    const listener = vi.fn();
    client.subscribe("sessions:session-9", listener);

    publicationHandlers[0]?.({ data: null });
    publicationHandlers[0]?.({ data: { type: "alert.created", version: "1", tenant_id: null, session_id: "session-9", occurred_at: null, payload: {} } });
    publicationHandlers[0]?.({ data: { type: "employee_intelligence.upsert" } });
    publicationHandlers[0]?.({ data: { type: "employee_intelligence.upsert", version: "1", tenant_id: null, session_id: "session-9", occurred_at: null, payload: {} } });

    expect(listener).toHaveBeenCalledTimes(1);
    expect(listener).toHaveBeenCalledWith({
      type: "employee_intelligence.upsert",
      version: "1",
      tenant_id: null,
      session_id: "session-9",
      occurred_at: null,
      payload: {},
    });
  });

  it("resolves the browser websocket url from window location", () => {
    Object.defineProperty(globalThis, "window", {
      configurable: true,
      value: {
        location: {
          protocol: "https:",
          hostname: "example.com",
          port: "3000",
        },
      },
    });

    expect(resolveLiveUrl()).toBe("wss://example.com:8080/connection/websocket");
  });
});

describe("live client type constraints", () => {
  it("correlates channel families with listener event families at compile time", () => {
    const client = createLiveClient({
      fetchToken: async () => "token-1",
      createTransport: () => ({
        connect: vi.fn(),
        disconnect: vi.fn(),
        on: vi.fn().mockReturnThis(),
        newSubscription: vi.fn(() => ({
          subscribe: vi.fn(),
          unsubscribe: vi.fn(),
          on: vi.fn().mockReturnThis(),
        })),
      }),
    });

    client.subscribe(sessionChannel("session-1"), (event) => {
      expect(event.type).toMatch(/^employee_intelligence\./);
    });
    client.subscribe(alertsChannel("session-1"), (event) => {
      expect(event.type).toMatch(/^alert\./);
    });
    client.subscribe(tenantOverviewChannel("tenant-1"), (event) => {
      expect(event.type).toMatch(/^overview\./);
    });

    // @ts-expect-error alerts cannot be subscribed through the session channel type
    client.subscribe(sessionChannel("session-2"), (event: { type: "alert.created" }) => event.type);
    // @ts-expect-error overview events cannot be subscribed through the alerts channel type
    client.subscribe(alertsChannel("session-2"), (event: { type: "overview.snapshot" }) => event.type);
  });
});
