import { alertsChannel, sessionChannel, tenantOverviewChannel } from "@/lib/live/channels";
import { getLiveClient, type LiveClient } from "@/lib/live/client";
import type {
  LiveChannel,
  LiveEventForChannel,
  LiveEventListener,
} from "@/lib/live/types";

export function subscribeToLiveChannel<TChannel extends LiveChannel>(
  channel: TChannel,
  listener: LiveEventListener<LiveEventForChannel<TChannel>>,
  client: LiveClient = getLiveClient(),
) {
  return client.subscribe(channel, listener);
}

export function subscribeToTenantOverview(
  tenantId: string,
  listener: LiveEventListener<LiveEventForChannel<ReturnType<typeof tenantOverviewChannel>>>,
  client?: LiveClient,
) {
  return subscribeToLiveChannel(tenantOverviewChannel(tenantId), listener, client);
}

export function subscribeToSession(
  sessionId: string,
  listener: LiveEventListener<LiveEventForChannel<ReturnType<typeof sessionChannel>>>,
  client?: LiveClient,
) {
  return subscribeToLiveChannel(sessionChannel(sessionId), listener, client);
}

export function subscribeToAlerts(
  sessionId: string,
  listener: LiveEventListener<LiveEventForChannel<ReturnType<typeof alertsChannel>>>,
  client?: LiveClient,
) {
  return subscribeToLiveChannel(alertsChannel(sessionId), listener, client);
}
