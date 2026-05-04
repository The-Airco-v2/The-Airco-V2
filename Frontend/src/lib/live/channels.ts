import type { AlertsChannel, SessionChannel, TenantOverviewChannel } from "@/lib/live/types";

export function tenantOverviewChannel(tenantId: string): TenantOverviewChannel {
  return `tenant:${tenantId}:overview`;
}

export function sessionChannel(sessionId: string): SessionChannel {
  return `sessions:${sessionId}`;
}

export function alertsChannel(sessionId: string): AlertsChannel {
  return `alerts:${sessionId}`;
}
