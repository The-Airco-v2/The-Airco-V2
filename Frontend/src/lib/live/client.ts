import { Centrifuge } from "centrifuge";
import { apiFetchJson } from "@/lib/api";
import type {
  LiveChannel,
  LiveEvent,
  LiveEventForChannel,
  LiveEventListener,
  LiveEventType,
} from "@/lib/live/types";

type TransportEvents = "connected" | "disconnected" | "error";
type TransportPublicationContext = { data: unknown };
type TransportEventHandler = (ctx: unknown) => void;
type PublicationHandler = (ctx: TransportPublicationContext) => void;

interface LiveSubscriptionTransport {
  on(event: "publication", listener: PublicationHandler): this;
  subscribe(): void;
  unsubscribe(): void;
  removeAllListeners?(event?: "publication"): this;
}

interface LiveTransport {
  on(event: TransportEvents, listener: TransportEventHandler): this;
  connect(): void;
  disconnect(): void;
  newSubscription(channel: string): LiveSubscriptionTransport;
}

type CreateTransport = (options: {
  url: string;
  getToken: () => Promise<string>;
}) => LiveTransport;

interface LiveClientOptions {
  url?: string;
  tokenEndpoint?: string;
  fetchToken?: () => Promise<string>;
  createTransport?: CreateTransport;
}

interface SubscriptionEntry {
  subscription: LiveSubscriptionTransport;
  listeners: Set<LiveEventListener>;
  isSubscribed: boolean;
}

const DEFAULT_TOKEN_ENDPOINT = "/api/v2/ws/token";

let sharedClient: LiveClient | null = null;

const CHANNEL_EVENT_TYPES = {
  tenant: new Set<LiveEventType>(["overview.snapshot", "overview.patch"]),
  sessions: new Set<LiveEventType>([
    "employee_intelligence.snapshot",
    "employee_intelligence.upsert",
  ]),
  alerts: new Set<LiveEventType>(["alert.created", "alert.updated", "alert.acknowledged"]),
} as const;

export function resolveLiveUrl() {
  const configuredUrl = (import.meta as ImportMeta & {
    env?: Record<string, string | undefined>;
  }).env?.VITE_WS_URL;
  if (typeof configuredUrl === "string" && configuredUrl.length > 0) {
    return configuredUrl;
  }

  if (typeof window === "undefined") {
    return "ws://localhost:8088/connection/websocket";
  }

  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const port = window.location.port === "3000" ? "8088" : window.location.port;
  const host = `${window.location.hostname}${port ? `:${port}` : ""}`;
  return `${protocol}//${host}/connection/websocket`;
}

function getChannelFamily(channel: LiveChannel) {
  if (channel.startsWith("tenant:")) {
    return "tenant";
  }
  if (channel.startsWith("sessions:")) {
    return "sessions";
  }
  return "alerts";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isLiveEventForChannel<TChannel extends LiveChannel>(
  channel: TChannel,
  value: unknown,
): value is LiveEventForChannel<TChannel> {
  if (!isRecord(value)) {
    return false;
  }

  const { type, version, tenant_id, session_id, occurred_at, payload } = value;
  if (typeof type !== "string" || !CHANNEL_EVENT_TYPES[getChannelFamily(channel)].has(type as LiveEventType)) {
    return false;
  }
  if (typeof version !== "string") {
    return false;
  }
  if (tenant_id !== null && typeof tenant_id !== "string") {
    return false;
  }
  if (session_id !== null && typeof session_id !== "string") {
    return false;
  }
  if (occurred_at !== null && typeof occurred_at !== "string") {
    return false;
  }

  return isRecord(payload);
}

function createCentrifugeTransport(options: {
  url: string;
  getToken: () => Promise<string>;
}): LiveTransport {
  return new Centrifuge(options.url, {
    getToken: async () => options.getToken(),
  });
}

async function fetchLiveToken(tokenEndpoint: string) {
  const response = await apiFetchJson<{ token: string }>(tokenEndpoint);
  return response.token;
}

export class LiveClient {
  private readonly url: string;
  private readonly fetchToken: () => Promise<string>;
  private readonly createTransport: CreateTransport;
  private transport: LiveTransport | null = null;
  private readonly subscriptions = new Map<LiveChannel, SubscriptionEntry>();

  constructor(options: LiveClientOptions = {}) {
    this.url = options.url ?? resolveLiveUrl();
    this.fetchToken = options.fetchToken ?? (() => fetchLiveToken(options.tokenEndpoint ?? DEFAULT_TOKEN_ENDPOINT));
    this.createTransport = options.createTransport ?? createCentrifugeTransport;
  }

  connect() {
    this.ensureTransport().connect();
  }

  disconnect() {
    this.transport?.disconnect();
  }

  reconnect() {
    const transport = this.ensureTransport();
    transport.disconnect();
    transport.connect();
  }

  private hasActiveListeners() {
    for (const entry of this.subscriptions.values()) {
      if (entry.listeners.size > 0) {
        return true;
      }
    }

    return false;
  }

  subscribe<TChannel extends LiveChannel>(
    channel: TChannel,
    listener: LiveEventListener<LiveEventForChannel<TChannel>>,
  ): () => void {
    const transport = this.ensureTransport();
    let entry = this.subscriptions.get(channel);

    if (!entry) {
      const listeners = new Set<LiveEventListener>();
      const subscription = transport.newSubscription(channel);
      subscription.on("publication", (ctx) => {
        if (!isLiveEventForChannel(channel, ctx.data)) {
          return;
        }

        for (const currentListener of listeners) {
          currentListener(ctx.data);
        }
      });
      subscription.subscribe();
      entry = { subscription, listeners, isSubscribed: true };
      this.subscriptions.set(channel, entry);
    }

    if (!entry.isSubscribed) {
      entry.subscription.subscribe();
      entry.isSubscribed = true;
    }

    entry.listeners.add(listener as LiveEventListener<LiveEvent>);
    transport.connect();

    return () => {
      const currentEntry = this.subscriptions.get(channel);
      if (!currentEntry) {
        return;
      }

      currentEntry.listeners.delete(listener as LiveEventListener);
      if (currentEntry.listeners.size > 0) {
        return;
      }

      currentEntry.subscription.unsubscribe();
      currentEntry.isSubscribed = false;

      if (!this.hasActiveListeners()) {
        this.transport?.disconnect();
      }
    };
  }

  private ensureTransport() {
    if (this.transport) {
      return this.transport;
    }

    this.transport = this.createTransport({
      url: this.url,
      getToken: this.fetchToken,
    });

    this.transport.on("connected", () => undefined);
    this.transport.on("disconnected", () => undefined);
    this.transport.on("error", () => undefined);

    return this.transport;
  }
}

export function createLiveClient(options: LiveClientOptions = {}) {
  return new LiveClient(options);
}

export function getLiveClient() {
  if (!sharedClient) {
    sharedClient = createLiveClient();
  }
  return sharedClient;
}

export function resetLiveClientForTests() {
  sharedClient = null;
}
