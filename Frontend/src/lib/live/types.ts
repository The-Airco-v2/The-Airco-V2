import type { Alert, EmployeeIntelligence, OverviewToday } from "@/types";

export type LiveEventType =
  | "overview.snapshot"
  | "overview.patch"
  | "employee_intelligence.snapshot"
  | "employee_intelligence.upsert"
  | "alert.created"
  | "alert.updated"
  | "alert.acknowledged";

export interface LiveEventEnvelope<TType extends LiveEventType, TPayload> {
  type: TType;
  version: string;
  tenant_id: string | null;
  session_id: string | null;
  occurred_at: string | null;
  payload: TPayload;
}

export interface OverviewSnapshotPayload {
  session: OverviewToday["session"];
  summary: OverviewToday["summary"];
  employees?: EmployeeIntelligence[];
}

export type OverviewPatchPayload = Partial<OverviewSnapshotPayload> & Record<string, unknown>;

export interface EmployeeIntelligenceSnapshotPayload {
  employees: EmployeeIntelligence[];
}

export type EmployeeIntelligenceUpsertPayload = {
  employee: EmployeeIntelligence;
} & Record<string, unknown>;

export type AlertLivePayload = Partial<Alert> & Record<string, unknown>;

export type OverviewSnapshotEvent = LiveEventEnvelope<"overview.snapshot", OverviewSnapshotPayload>;
export type OverviewPatchEvent = LiveEventEnvelope<"overview.patch", OverviewPatchPayload>;
export type EmployeeIntelligenceSnapshotEvent = LiveEventEnvelope<
  "employee_intelligence.snapshot",
  EmployeeIntelligenceSnapshotPayload
>;
export type EmployeeIntelligenceUpsertEvent = LiveEventEnvelope<
  "employee_intelligence.upsert",
  EmployeeIntelligenceUpsertPayload
>;
export type AlertCreatedEvent = LiveEventEnvelope<"alert.created", AlertLivePayload>;
export type AlertUpdatedEvent = LiveEventEnvelope<"alert.updated", AlertLivePayload>;
export type AlertAcknowledgedEvent = LiveEventEnvelope<"alert.acknowledged", AlertLivePayload>;

export type TenantOverviewEvent = OverviewSnapshotEvent | OverviewPatchEvent;
export type SessionLiveEvent = EmployeeIntelligenceSnapshotEvent | EmployeeIntelligenceUpsertEvent;
export type AlertsLiveEvent = AlertCreatedEvent | AlertUpdatedEvent | AlertAcknowledgedEvent;
export type LiveEvent = TenantOverviewEvent | SessionLiveEvent | AlertsLiveEvent;

export type TenantOverviewChannel = `tenant:${string}:overview`;
export type SessionChannel = `sessions:${string}`;
export type AlertsChannel = `alerts:${string}`;
export type LiveChannel = TenantOverviewChannel | SessionChannel | AlertsChannel;

export type ChannelEventMap = {
  [K in TenantOverviewChannel]: TenantOverviewEvent;
} & {
  [K in SessionChannel]: SessionLiveEvent;
} & {
  [K in AlertsChannel]: AlertsLiveEvent;
};

export type LiveEventForChannel<TChannel extends LiveChannel> = ChannelEventMap[TChannel];
export type LiveEventListener<TEvent extends LiveEvent = LiveEvent> = (event: TEvent) => void;
