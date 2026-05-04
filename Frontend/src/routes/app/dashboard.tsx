import {
  Activity,
  AlertTriangle,
  Bell,
  Camera,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Clock3,
  Eye,
  MapPin,
  Play,
  ShieldAlert,
  Sparkles,
  Timer,
  UserCheck,
  Users,
} from "lucide-react";
import { KpiCard } from "@/components/shared/KpiCard";
import { PageHeader } from "@/components/shared/PageHeader";
import { StatusBadge } from "@/components/shared/StatusBadge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useAlerts, useAcknowledgeAlert } from "@/hooks/useAlerts";
import { useLiveAlerts } from "@/hooks/useLiveAlerts";
import { useLiveEmployeeIntelligence } from "@/hooks/useLiveEmployeeIntelligence";
import { useUnknownPersonDetail, useUnknownPersons } from "@/hooks/usePersons";
import { useLiveOverview } from "@/hooks/useLiveOverview";
import { useEmployeeIntelligence, useOverviewToday } from "@/hooks/useSessions";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import type { EmployeeIntelligence, UnknownPersonSummary, UnknownPersonTimelineMoment } from "@/types";
import { useEffect, useMemo, useRef, useState } from "react";

function formatRelative(iso: string | null) {
  if (!iso) {
    return "—";
  }
  const d = new Date(iso);
  const diff = Date.now() - d.getTime();
  if (diff < 60_000) {
    return "just now";
  }
  if (diff < 3_600_000) {
    return `${Math.floor(diff / 60_000)}m ago`;
  }
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function formatTimestamp(iso: string | null) {
  if (!iso) {
    return "—";
  }
  return new Date(iso).toLocaleString([], {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatDuration(seconds: number | null | undefined) {
  if (!seconds || seconds <= 0) {
    return "—";
  }
  const totalMinutes = Math.round(seconds / 60);
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  if (hours > 0) {
    return `${hours}h ${minutes}m`;
  }
  return `${minutes}m`;
}

function activityLabel(person: UnknownPersonSummary) {
  if (person.risk_level === "high") {
    return "Flagged";
  }
  if (person.is_active) {
    return "Moving";
  }
  return "Idle";
}

function activityClasses(person: UnknownPersonSummary) {
  if (person.risk_level === "high") {
    return "bg-red-500/10 text-red-400";
  }
  if (person.is_active) {
    return "bg-sky-500/10 text-sky-400";
  }
  return "bg-zinc-800 text-zinc-400";
}

function formatPercent(value: number | null | undefined) {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return "—";
  }
  return `${Math.round(value * 100)}%`;
}

function continuityLabel(confidence: number) {
  if (confidence >= 0.75) {
    return "High continuity";
  }
  if (confidence >= 0.5) {
    return "Moderate continuity";
  }
  return "Limited continuity";
}

function continuityClasses(confidence: number) {
  if (confidence >= 0.75) {
    return "border-emerald-500/30 bg-emerald-500/10 text-emerald-300";
  }
  if (confidence >= 0.5) {
    return "border-amber-500/30 bg-amber-500/10 text-amber-300";
  }
  return "border-red-500/30 bg-red-500/10 text-red-300";
}

function continuityReasonLabel(reason: string) {
  switch (reason) {
    case "first_seen_and_latest_seen":
      return "The timeline includes both the first and most recent sightings for this person.";
    case "camera_transition":
      return "The sightings include a camera-to-camera handoff that fits one continuous track.";
    case "zone_transition":
      return "The evidence shows movement between zones that supports one continuous path.";
    case "alert_evidence":
      return "An alert-backed frame is part of the evidence trail for this person.";
    case "dwell_checkpoints":
      return "Timed dwell checkpoints fill in longer stays without flooding the timeline.";
    case "curated_timeline":
      return "The backend returned a curated set of representative moments for review.";
    default:
      return "The system found supporting timeline evidence for this grouping.";
  }
}

function momentSelectionReasonLabel(reason: string | null | undefined) {
  switch (reason) {
    case "alert_evidence":
      return "This marker is backed by alert evidence.";
    case "snapshot_evidence":
      return "This marker uses a representative frame from the tracked timeline.";
    case "first_frame_in_person_history":
      return "This is the earliest available frame for this person in the session.";
    case "latest_frame_in_person_history":
      return "This is the latest available frame for this person in the session.";
    case "empty_placeholder":
      return "This marker helps explain the track, but no preview image was available.";
    default:
      return "This marker was selected as one of the key moments in the evidence timeline.";
  }
}

function momentKindLabel(kind: UnknownPersonTimelineMoment["kind"]) {
  switch (kind) {
    case "first_seen":
      return "First seen";
    case "dwell_checkpoint":
      return "Dwell checkpoint";
    case "camera_transition":
      return "Camera transition";
    case "zone_transition":
      return "Zone transition";
    case "alert_backed":
      return "Alert evidence";
    case "latest_seen":
      return "Latest seen";
  }
}

function momentKindIcon(kind: UnknownPersonTimelineMoment["kind"]) {
  switch (kind) {
    case "first_seen":
    case "latest_seen":
      return Eye;
    case "dwell_checkpoint":
      return Timer;
    case "camera_transition":
      return Camera;
    case "zone_transition":
      return MapPin;
    case "alert_backed":
      return AlertTriangle;
  }
}

function momentPreviewUrl(moment: Pick<UnknownPersonTimelineMoment, "image_url" | "thumbnail_url">) {
  return moment.image_url ?? moment.thumbnail_url ?? null;
}

function momentLocationLabel(moment: UnknownPersonTimelineMoment) {
  if (moment.kind === "camera_transition") {
    const from = moment.camera_name ?? "Unknown camera";
    const to = moment.to_camera_name ?? "next camera";
    return `${from} -> ${to}`;
  }
  if (moment.kind === "zone_transition") {
    const from = moment.zone ?? "Unknown zone";
    const to = moment.to_zone ?? "next zone";
    return `${from} -> ${to}`;
  }
  return moment.camera_name ?? moment.zone ?? "Unknown location";
}

function timelineWindowLabel(windowStart: string | null, windowEnd: string | null) {
  if (windowStart && windowEnd) {
    return `${formatTimestamp(windowStart)} to ${formatTimestamp(windowEnd)}`;
  }
  if (windowEnd) {
    return `Up to ${formatTimestamp(windowEnd)}`;
  }
  if (windowStart) {
    return `From ${formatTimestamp(windowStart)}`;
  }
  return "Timeline window unavailable";
}

type InsightAccent = "emerald" | "sky" | "amber" | "violet" | "red";

type WhoInsight = {
  id: "who";
  question: string;
  summary: string;
  icon: typeof UserCheck;
  accent: InsightAccent;
  details: Array<{
    id: string;
    name: string;
    status: "working" | "idle" | "walking";
    location: string;
    time: string;
  }>;
};

type WhereInsight = {
  id: "where";
  question: string;
  summary: string;
  icon: typeof MapPin;
  accent: InsightAccent;
  details: Array<{
    id: string;
    zone: string;
    count: number;
    activity: string;
  }>;
};

type WhatInsight = {
  id: "what";
  question: string;
  summary: string;
  icon: typeof Activity;
  accent: InsightAccent;
  details: Array<{
    id: string;
    activity: string;
    count: number;
    percentage: number;
  }>;
};

type HowLongInsight = {
  id: "how-long";
  question: string;
  summary: string;
  icon: typeof Timer;
  accent: InsightAccent;
  details: Array<{
    id: string;
    range: string;
    count: number;
    employees: string[];
  }>;
};

type ViolationsInsight = {
  id: "violations";
  question: string;
  summary: string;
  icon: typeof AlertTriangle;
  accent: InsightAccent;
  details: Array<{
    id: string;
    type: string;
    employee: string;
    time: string;
    severity: "low" | "medium" | "high" | "critical";
  }>;
};

type DashboardInsight =
  | WhoInsight
  | WhereInsight
  | WhatInsight
  | HowLongInsight
  | ViolationsInsight;

function personDurationSeconds(person: EmployeeIntelligence) {
  const start = person.presence.entered_at ? new Date(person.presence.entered_at).getTime() : null;
  const endSource = person.presence.last_seen ?? person.presence.entered_at;
  const end = endSource ? new Date(endSource).getTime() : null;
  if (!start || !end || end <= start) {
    return 0;
  }
  return Math.round((end - start) / 1000);
}

function buildDashboardInsights(
  intelligence: EmployeeIntelligence[] | undefined,
  alerts: Array<{ severity: string; message: string; created_at: string }> | undefined,
): DashboardInsight[] {
  const employees = Array.isArray(intelligence) ? intelligence : [];
  const presentEmployees = employees.filter((employee) => employee.presence.is_present);

  const whoSummary = `${presentEmployees.length} employee${presentEmployees.length === 1 ? "" : "s"}`;
  const whoDetails = presentEmployees
    .slice()
    .sort((left, right) => personDurationSeconds(right) - personDurationSeconds(left))
    .slice(0, 6)
    .map((employee) => ({
      id: employee.employee_id ?? employee.employee_name,
      name: employee.employee_name,
      status: employee.live_status === "unknown" ? "idle" : employee.live_status,
      location: employee.location.current_zone ?? "Unknown zone",
      time: formatDuration(personDurationSeconds(employee)),
    }));

  const zoneCounts = new Map<string, number>();
  for (const employee of presentEmployees) {
    const zone = employee.location.current_zone ?? "Unknown zone";
    zoneCounts.set(zone, (zoneCounts.get(zone) ?? 0) + 1);
  }
  const whereDetails = Array.from(zoneCounts.entries())
    .sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0]))
    .slice(0, 6)
    .map(([zone, count]) => ({
      id: zone,
      zone,
      count,
      activity: count >= 3 ? "High" : count === 2 ? "Medium" : "Low",
    }));
  const whereSummary = `${whereDetails.length} zone${whereDetails.length === 1 ? "" : "s"} active`;

  const statusCounts = new Map<string, number>();
  for (const employee of presentEmployees) {
    const status = employee.live_status === "unknown" ? "idle" : employee.live_status;
    statusCounts.set(status, (statusCounts.get(status) ?? 0) + 1);
  }
  const totalStatuses = Array.from(statusCounts.values()).reduce((sum, value) => sum + value, 0);
  const statusLabelMap: Record<string, string> = {
    working: "Working",
    idle: "Idle",
    walking: "Moving",
  };
  const whatDetails = Array.from(statusCounts.entries())
    .sort((left, right) => right[1] - left[1])
    .map(([status, count]) => ({
      id: status,
      activity: statusLabelMap[status] ?? status,
      count,
      percentage: totalStatuses > 0 ? Math.round((count / totalStatuses) * 100) : 0,
    }));
  const workingCount = statusCounts.get("working") ?? 0;
  const idleCount = statusCounts.get("idle") ?? 0;
  const whatSummary = `${workingCount} working, ${idleCount} idle`;

  const durationBuckets = [
    { label: "5+ hours", minSeconds: 5 * 3600, maxSeconds: Number.POSITIVE_INFINITY },
    { label: "4-5 hours", minSeconds: 4 * 3600, maxSeconds: 5 * 3600 },
    { label: "3-4 hours", minSeconds: 3 * 3600, maxSeconds: 4 * 3600 },
    { label: "< 3 hours", minSeconds: 0, maxSeconds: 3 * 3600 },
  ];
  const durationDetails = durationBuckets.map((bucket) => {
    const employeesInBucket = presentEmployees.filter((employee) => {
      const seconds = personDurationSeconds(employee);
      return seconds >= bucket.minSeconds && seconds < bucket.maxSeconds;
    });
    return {
      id: bucket.label,
      range: bucket.label,
      count: employeesInBucket.length,
      employees: employeesInBucket.map((employee) => employee.employee_name),
    };
  });
  const avgDurationSeconds = presentEmployees.length
    ? Math.round(
        presentEmployees.reduce((sum, employee) => sum + personDurationSeconds(employee), 0) /
          presentEmployees.length,
      )
    : 0;
  const howLongSummary = `Avg: ${formatDuration(avgDurationSeconds)}`;

  const phoneViolations: ViolationsInsight["details"] = presentEmployees
    .filter((employee) => employee.violations.phone_violation)
    .map((employee) => ({
      id: `${employee.employee_id ?? employee.employee_name}-phone`,
      type: "Phone Usage",
      employee: employee.employee_name,
      time: formatRelative(employee.presence.last_seen),
      severity: "medium" as const,
    }));
  const zoneViolations: ViolationsInsight["details"] = presentEmployees
    .filter((employee) => employee.violations.restricted_zone_violation)
    .map((employee) => ({
      id: `${employee.employee_id ?? employee.employee_name}-zone`,
      type: "Restricted Zone",
      employee: employee.employee_name,
      time: formatRelative(employee.presence.last_seen),
      severity: "critical" as const,
    }));
  const alertDerivedViolations: ViolationsInsight["details"] = (alerts ?? [])
    .filter((alert) => /idle/i.test(alert.message))
    .slice(0, 3)
    .map((alert) => ({
      id: `${alert.created_at}-${alert.message}`,
      type: "Idle Alert",
      employee: "Session alert",
      time: formatRelative(alert.created_at),
      severity: alert.severity === "low" || alert.severity === "medium" || alert.severity === "high" || alert.severity === "critical"
        ? alert.severity
        : "low",
    }));
  const violationsDetails = [...zoneViolations, ...phoneViolations, ...alertDerivedViolations].slice(0, 6);
  const violationsSummary = `${violationsDetails.length} detected`;

  return [
    {
      id: "who",
      question: "Who is present?",
      summary: whoSummary,
      icon: UserCheck,
      accent: "emerald" as const,
      details: whoDetails,
    },
    {
      id: "where",
      question: "Where are they?",
      summary: whereSummary,
      icon: MapPin,
      accent: "sky" as const,
      details: whereDetails,
    },
    {
      id: "what",
      question: "What are they doing?",
      summary: whatSummary,
      icon: Activity,
      accent: "amber" as const,
      details: whatDetails,
    },
    {
      id: "how-long",
      question: "How long did they stay?",
      summary: howLongSummary,
      icon: Timer,
      accent: "violet" as const,
      details: durationDetails,
    },
    {
      id: "violations",
      question: "Any violations?",
      summary: violationsSummary,
      icon: AlertTriangle,
      accent: "red" as const,
      details: violationsDetails,
    },
  ];
}

function insightAccentClasses(accent: "emerald" | "sky" | "amber" | "violet" | "red") {
  switch (accent) {
    case "emerald":
      return "bg-emerald-500/10 text-emerald-400";
    case "sky":
      return "bg-sky-500/10 text-sky-400";
    case "amber":
      return "bg-amber-500/10 text-amber-400";
    case "violet":
      return "bg-violet-500/10 text-violet-400";
    case "red":
      return "bg-red-500/10 text-red-400";
  }
}

function InsightCard({
  insight,
}: {
  insight: DashboardInsight;
}) {
  const [open, setOpen] = useState(false);
  const Icon = insight.icon;

  return (
    <div className="overflow-hidden rounded-xl border border-zinc-800 bg-zinc-900/60">
      <button
        type="button"
        onClick={() => setOpen((current) => !current)}
        className="flex w-full items-center justify-between px-4 py-3 text-left transition-colors hover:bg-zinc-800/40"
      >
        <div className="flex items-center gap-3">
          <div className={cn("flex h-8 w-8 items-center justify-center rounded-lg", insightAccentClasses(insight.accent))}>
            <Icon className="h-4 w-4" />
          </div>
          <div>
            <p className="text-sm font-medium text-zinc-100">{insight.question}</p>
            <p className="text-xs text-zinc-500">{insight.summary}</p>
          </div>
        </div>
        <div className="flex h-7 w-7 items-center justify-center rounded-full bg-zinc-800 text-zinc-400">
          {open ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
        </div>
      </button>
      {open && (
        <div className="border-t border-zinc-800 px-4 py-3">
          <div className="space-y-2">
            {insight.id === "who" &&
              insight.details.map((item) => (
                <div key={item.id} className="flex items-center justify-between rounded-lg bg-zinc-800/60 p-3">
                  <div>
                    <p className="text-sm font-medium text-zinc-100">{item.name}</p>
                    <p className="text-xs text-zinc-500">{item.location}</p>
                  </div>
                  <div className="text-right">
                    <StatusBadge status={item.status === "walking" ? "walking" : item.status === "working" ? "working" : "idle"} />
                    <p className="mt-1 text-xs text-zinc-500">{item.time}</p>
                  </div>
                </div>
              ))}
            {insight.id === "where" &&
              insight.details.map((item) => (
                <div key={item.id} className="flex items-center justify-between rounded-lg bg-zinc-800/60 p-3">
                  <div>
                    <p className="text-sm font-medium text-zinc-100">{item.zone}</p>
                    <p className="text-xs text-zinc-500">{item.activity} activity</p>
                  </div>
                  <div className="rounded-md bg-zinc-900 px-2 py-1 text-xs font-semibold text-zinc-200">{item.count}</div>
                </div>
              ))}
            {insight.id === "what" &&
              insight.details.map((item) => (
                <div key={item.id} className="space-y-1" >
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-zinc-300">{item.activity}</span>
                    <span className="font-medium text-zinc-100">
                      {item.count} ({item.percentage}%)
                    </span>
                  </div>
                  <div className="h-1.5 rounded-full bg-zinc-800">
                    <div className="h-1.5 rounded-full bg-sky-500" style={{ width: `${item.percentage}%` }} />
                  </div>
                </div>
              ))}
            {insight.id === "how-long" &&
              insight.details.map((item) => (
                <div key={item.id} className="rounded-lg bg-zinc-800/60 p-3">
                  <div className="flex items-center justify-between">
                    <p className="text-sm font-medium text-zinc-100">{item.range}</p>
                    <span className="text-xs font-semibold text-zinc-300">{item.count}</span>
                  </div>
                  {item.employees.length ? (
                    <p className="mt-1 text-xs text-zinc-500">{item.employees.join(", ")}</p>
                  ) : (
                    <p className="mt-1 text-xs text-zinc-600">No employees in this range</p>
                  )}
                </div>
              ))}
            {insight.id === "violations" &&
              insight.details.map((item) => (
                <div key={item.id} className="flex items-center justify-between rounded-lg bg-zinc-800/60 p-3">
                  <div>
                    <p className="text-sm font-medium text-zinc-100">{item.type}</p>
                    <p className="text-xs text-zinc-500">{item.employee}</p>
                  </div>
                  <div className="text-right">
                    <StatusBadge status={item.severity} />
                    <p className="mt-1 text-xs text-zinc-500">{item.time}</p>
                  </div>
                </div>
              ))}
            {!insight.details.length && <p className="text-sm text-zinc-500">No current data.</p>}
          </div>
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Lightbox — full-screen image overlay, click backdrop to close
// ─────────────────────────────────────────────────────────────────────────────
function Lightbox({ src, alt, onClose }: { src: string; alt: string; onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/90 p-4"
      onClick={onClose}
    >
      <img
        src={src}
        alt={alt}
        className="max-h-full max-w-full object-contain"
        onClick={(e) => e.stopPropagation()}
      />
      <button
        type="button"
        onClick={onClose}
        className="absolute right-4 top-4 rounded-full bg-zinc-900 p-2 text-zinc-300 hover:bg-zinc-800"
      >
        ✕
      </button>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// DayTimeline — full-day horizontal bar with positioned markers
// ─────────────────────────────────────────────────────────────────────────────
function DayTimeline({
  moments,
  windowStart,
  selectedId,
  onSelect,
}: {
  moments: UnknownPersonTimelineMoment[];
  windowStart: string | null;
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  // Snap to midnight of the session day so the bar always covers 24 hours
  const dayStart = useMemo(() => {
    const d = windowStart ? new Date(windowStart) : new Date();
    return new Date(d.getFullYear(), d.getMonth(), d.getDate(), 0, 0, 0).getTime();
  }, [windowStart]);
  const dayEnd = dayStart + 86400000; // +24 h

  const hourLabels = [0, 6, 12, 18, 24].map((h) => ({
    label: h === 0 || h === 24 ? "12 AM" : h === 12 ? "12 PM" : h < 12 ? `${h} AM` : `${h - 12} PM`,
    pct: (h / 24) * 100,
  }));

  function pct(ts: number) {
    return Math.max(0, Math.min(100, ((ts - dayStart) / (dayEnd - dayStart)) * 100));
  }

  return (
    <div className="select-none">
      {/* Hour labels */}
      <div className="relative h-4 mb-1">
        {hourLabels.map(({ label, pct: p }) => (
          <span
            key={label}
            className="absolute -translate-x-1/2 text-[9px] text-zinc-600"
            style={{ left: `${p}%` }}
          >
            {label}
          </span>
        ))}
      </div>

      {/* Timeline bar + markers */}
      <div className="relative h-10">
        {/* Track */}
        <div className="absolute top-1/2 left-0 right-0 h-px -translate-y-1/2 bg-zinc-700" />
        {/* Hour ticks */}
        {hourLabels.map(({ label, pct: p }) => (
          <div
            key={label}
            className="absolute top-1/2 h-2 w-px -translate-y-1/2 bg-zinc-700"
            style={{ left: `${p}%` }}
          />
        ))}
        {/* Event markers */}
        {moments.map((moment) => {
          const ts = moment.occurred_at ? new Date(moment.occurred_at).getTime() : dayStart;
          const p = pct(ts);
          const isSelected = moment.id === selectedId;
          const hasImage = !!momentPreviewUrl(moment);
          const isAlert = moment.kind === "alert_backed";
          return (
            <button
              key={moment.id}
              type="button"
              onClick={() => onSelect(moment.id)}
              style={{ left: `${p}%` }}
              className="absolute top-1/2 -translate-x-1/2 -translate-y-1/2 group"
              title={`${momentKindLabel(moment.kind)} · ${momentLocationLabel(moment)}`}
            >
              {/* Stem */}
              <div
                className={cn(
                  "absolute bottom-full left-1/2 -translate-x-1/2 w-px",
                  isSelected ? "h-3 bg-sky-400" : "h-2 bg-zinc-500",
                )}
              />
              {/* Dot */}
              <div
                className={cn(
                  "relative z-10 rounded-full border transition-all",
                  isSelected
                    ? "h-3.5 w-3.5 border-sky-400 bg-sky-400 shadow-[0_0_6px_1px_rgba(56,189,248,0.5)]"
                    : isAlert
                      ? "h-2.5 w-2.5 border-red-500 bg-red-500/70 hover:scale-125"
                      : hasImage
                        ? "h-2.5 w-2.5 border-zinc-400 bg-zinc-200 hover:scale-125"
                        : "h-2 w-2 border-zinc-600 bg-zinc-700 hover:scale-125",
                )}
              />
            </button>
          );
        })}
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// MarkerDetail — panel shown below the timeline when a marker is selected
// ─────────────────────────────────────────────────────────────────────────────
function MarkerDetail({
  moment,
  onEnlarge,
}: {
  moment: UnknownPersonTimelineMoment;
  onEnlarge: (src: string) => void;
}) {
  const Icon = momentKindIcon(moment.kind);
  const previewUrl = momentPreviewUrl(moment);

  return (
    <div className="flex gap-3 rounded-sm border border-zinc-800 bg-zinc-900 p-3">
      {/* Image — click to enlarge */}
      <div
        className="h-32 w-44 shrink-0 cursor-pointer overflow-hidden rounded-sm border border-zinc-700 bg-zinc-950"
        onClick={() => previewUrl && onEnlarge(previewUrl)}
        title={previewUrl ? "Click to enlarge" : undefined}
      >
        {previewUrl ? (
          <img
            src={previewUrl}
            alt={momentKindLabel(moment.kind)}
            className="h-full w-full object-cover transition-opacity hover:opacity-90"
            loading="lazy"
          />
        ) : (
          <div className="flex h-full w-full flex-col items-center justify-center gap-1 text-zinc-600">
            <Icon className="h-5 w-5" />
            <span className="text-[10px]">No image</span>
          </div>
        )}
      </div>

      {/* Details */}
      <div className="flex-1 space-y-1.5 py-0.5">
        <div className="flex items-center gap-2">
          <span
            className={cn(
              "rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide",
              moment.kind === "alert_backed"
                ? "bg-red-500/15 text-red-400"
                : moment.kind === "first_seen"
                  ? "bg-emerald-500/15 text-emerald-400"
                  : moment.kind === "latest_seen"
                    ? "bg-amber-500/15 text-amber-400"
                    : "bg-zinc-800 text-zinc-400",
            )}
          >
            {momentKindLabel(moment.kind)}
          </span>
          {previewUrl && (
            <button
              type="button"
              onClick={() => onEnlarge(previewUrl)}
              className="text-[10px] text-zinc-500 hover:text-zinc-300"
            >
              Enlarge ↗
            </button>
          )}
        </div>
        <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
          {moment.camera_name && (
            <div>
              <span className="text-zinc-600">Camera </span>
              <span className="text-zinc-300">{moment.camera_name}</span>
            </div>
          )}
          {moment.zone && (
            <div>
              <span className="text-zinc-600">Zone </span>
              <span className="text-zinc-300">{moment.zone}</span>
            </div>
          )}
          {(moment.kind === "camera_transition" || moment.kind === "zone_transition") && moment.to_camera_name && (
            <div>
              <span className="text-zinc-600">→ Camera </span>
              <span className="text-zinc-300">{moment.to_camera_name}</span>
            </div>
          )}
          {moment.to_zone && (
            <div>
              <span className="text-zinc-600">→ Zone </span>
              <span className="text-zinc-300">{moment.to_zone}</span>
            </div>
          )}
          {moment.alert_type && (
            <div>
              <span className="text-zinc-600">Alert </span>
              <span className="text-red-400">{moment.alert_type.replace(/_/g, " ")}</span>
            </div>
          )}
          <div>
            <span className="text-zinc-600">Time </span>
            <span className="text-zinc-300">{formatTimestamp(moment.occurred_at)}</span>
          </div>
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// ViolationChips — shows what flags exist for this person, clearly
// ─────────────────────────────────────────────────────────────────────────────
function ViolationChips({
  violations,
  riskContext,
}: {
  violations: { phone_usage_minutes: number; active_alert_types: string[]; identity_conflict: boolean; low_face_confidence: boolean };
  riskContext: { risk_factors: string[]; risk_level: string };
}) {
  type Chip = { label: string; icon: string; style: string };
  const chips: Chip[] = [];

  if (violations.phone_usage_minutes > 0)
    chips.push({ label: `Phone ${violations.phone_usage_minutes}m`, icon: "📱", style: "bg-amber-500/10 text-amber-300 border-amber-500/20" });
  if (violations.active_alert_types.includes("unknown_person"))
    chips.push({ label: "Unknown identity", icon: "👤", style: "bg-red-500/10 text-red-300 border-red-500/20" });
  if (violations.active_alert_types.some((t) => t.includes("zone")))
    chips.push({ label: "Restricted zone", icon: "🚫", style: "bg-red-500/10 text-red-300 border-red-500/20" });
  if (riskContext.risk_factors.includes("extended_dwell"))
    chips.push({ label: "Extended dwell", icon: "⏱", style: "bg-amber-500/10 text-amber-300 border-amber-500/20" });
  if (violations.identity_conflict)
    chips.push({ label: "Identity conflict", icon: "⚠", style: "bg-red-500/10 text-red-300 border-red-500/20" });
  if (violations.low_face_confidence)
    chips.push({ label: "Low face confidence", icon: "🔍", style: "bg-zinc-700/50 text-zinc-400 border-zinc-600/30" });

  if (!chips.length) return null;

  return (
    <div className="border-t border-zinc-800 pt-3">
      <p className="mb-2 text-[10px] font-semibold uppercase tracking-widest text-zinc-500">Flags</p>
      <div className="flex flex-wrap gap-1.5">
        {chips.map((chip) => (
          <span
            key={chip.label}
            className={cn("inline-flex items-center gap-1 rounded border px-2 py-1 text-[11px] font-medium", chip.style)}
          >
            <span>{chip.icon}</span>
            {chip.label}
          </span>
        ))}
      </div>
    </div>
  );
}

function UnknownPersonCard({
  person,
  sessionId,
}: {
  person: UnknownPersonSummary;
  sessionId: string | null;
}) {
  const [open, setOpen] = useState(false);
  const [selectedMomentId, setSelectedMomentId] = useState<string | null>(null);
  const [lightboxSrc, setLightboxSrc] = useState<string | null>(null);
  const navigate = useNavigate();
  const { data: detail, isLoading } = useUnknownPersonDetail(open ? person.person_id : null, sessionId);
  const label = person.display_name;

  // Merge storyboard (has images) + timeline moments, deduplicated, sorted chronologically.
  const timelineMoments = useMemo(() => {
    if (!detail) return [];
    const storyboard: UnknownPersonTimelineMoment[] = Array.isArray(detail.storyboard) ? detail.storyboard : [];
    const moments: UnknownPersonTimelineMoment[] = Array.isArray(detail.timeline?.moments) ? detail.timeline.moments : [];
    const storyboardIds = new Set(storyboard.map((m) => m.id));
    const combined = [...storyboard, ...moments.filter((m) => !storyboardIds.has(m.id))];
    return combined.sort(
      (a, b) =>
        (a.occurred_at ? new Date(a.occurred_at).getTime() : 0) -
        (b.occurred_at ? new Date(b.occurred_at).getTime() : 0),
    );
  }, [detail]);

  // Auto-select first marker with an image when detail loads
  useEffect(() => {
    if (!detail || !open) return;
    setSelectedMomentId(
      timelineMoments.find((m) => momentPreviewUrl(m))?.id ?? timelineMoments[0]?.id ?? null,
    );
  }, [detail?.person.person_id, open]);

  const riskBorderColor =
    person.risk_level === "high"
      ? "border-l-red-500"
      : person.risk_level === "medium"
        ? "border-l-amber-500"
        : "border-l-zinc-700";

  return (
    <div className={cn("overflow-hidden border border-zinc-800 border-l-[3px] bg-zinc-950", riskBorderColor)}>
      {/* ── CARD HEADER — always visible ── */}
      <button
        type="button"
        onClick={() => setOpen((c) => !c)}
        className="flex w-full items-start gap-4 px-4 py-3 text-left transition-colors hover:bg-zinc-900/50"
      >
        {/* Portrait thumbnail — large enough to identify the person without opening */}
        <div className="h-28 w-20 shrink-0 overflow-hidden rounded-sm border border-zinc-800 bg-zinc-900">
          {person.best_thumbnail_url ? (
            <img
              src={person.best_thumbnail_url}
              alt={label}
              className="h-full w-full object-cover"
              loading="lazy"
            />
          ) : (
            <div className="flex h-full w-full items-center justify-center">
              <Eye className="h-5 w-5 text-zinc-600" />
            </div>
          )}
        </div>

        {/* Identity summary */}
        <div className="min-w-0 flex-1 py-0.5">
          <div className="flex items-start justify-between gap-2">
            <p className="text-sm font-semibold text-zinc-100">{label}</p>
            {person.risk_level === "high" && (
              <span className="shrink-0 rounded bg-red-500/15 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-red-400">
                High
              </span>
            )}
          </div>
          <p className="mt-1 text-xs text-zinc-500">
            {[person.current_camera, person.current_zone].filter(Boolean).join(" · ") || "Location pending"}
          </p>
          <div className="mt-2.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
            <span className="text-zinc-400">
              <span className="font-medium text-zinc-200">{formatDuration(person.dwell_seconds)}</span> dwell
            </span>
            <span className="text-zinc-500">
              last seen <span className="text-zinc-300">{formatRelative(person.last_seen_at)}</span>
            </span>
            {person.active_alert_count > 0 && (
              <span className="font-medium text-red-400">
                {person.active_alert_count} alert{person.active_alert_count !== 1 ? "s" : ""}
              </span>
            )}
          </div>
          {person.is_active && (
            <div className="mt-2 flex items-center gap-1.5">
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-400" />
              <span className="text-[11px] text-emerald-400">Tracked now</span>
            </div>
          )}
        </div>

        <div className="mt-1 shrink-0">
          {open ? <ChevronUp className="h-4 w-4 text-zinc-500" /> : <ChevronDown className="h-4 w-4 text-zinc-500" />}
        </div>
      </button>

      {/* ── EXPANDED DETAIL ── */}
      {open && (
        <div className="border-t border-zinc-800">
          {isLoading ? (
            <div className="space-y-3 p-4">
              <Skeleton className="h-10 w-full bg-zinc-900" />
              <Skeleton className="h-40 w-full bg-zinc-900" />
            </div>
          ) : detail ? (
            <div className="space-y-4 p-4">

              {/* ── DAY TIMELINE ── */}
              <div>
                <p className="mb-3 text-[10px] font-semibold uppercase tracking-widest text-zinc-500">
                  Timeline · {timelineMoments.length} event{timelineMoments.length !== 1 ? "s" : ""}
                  {timelineMoments.length > 0 && (
                    <span className="ml-2 font-normal normal-case text-zinc-600">
                      — click a marker to inspect
                    </span>
                  )}
                </p>
                {timelineMoments.length > 0 ? (
                  <DayTimeline
                    moments={timelineMoments}
                    windowStart={detail.timeline.window_start}
                    selectedId={selectedMomentId}
                    onSelect={setSelectedMomentId}
                  />
                ) : (
                  <p className="text-xs text-zinc-600">
                    No timeline events yet. Evidence accumulates as the person continues to be tracked.
                  </p>
                )}
              </div>

              {/* ── SELECTED MARKER DETAIL ── */}
              {selectedMomentId && (() => {
                const moment = timelineMoments.find((m) => m.id === selectedMomentId);
                return moment ? (
                  <MarkerDetail
                    moment={moment}
                    onEnlarge={setLightboxSrc}
                  />
                ) : null;
              })()}

              {/* ── SUMMARY STATS ── */}
              <div className="grid grid-cols-2 gap-x-6 gap-y-2.5 border-t border-zinc-800 pt-3 sm:grid-cols-4">
                {[
                  { label: "First seen", value: formatTimestamp(detail.person.first_seen_at) },
                  { label: "Last seen", value: formatTimestamp(detail.person.last_seen_at) },
                  { label: "Total dwell", value: formatDuration(detail.dwell_analysis.total_seconds) },
                  {
                    label: "Risk level",
                    value: `${detail.risk_context.risk_level} · ${detail.violations.active_alert_count} alert${detail.violations.active_alert_count !== 1 ? "s" : ""}`,
                    accent:
                      detail.risk_context.risk_level === "high"
                        ? "text-red-400"
                        : detail.risk_context.risk_level === "medium"
                          ? "text-amber-400"
                          : "text-zinc-200",
                  },
                ].map(({ label: statLabel, value, accent }) => (
                  <div key={statLabel}>
                    <p className="text-[10px] uppercase tracking-wide text-zinc-600">{statLabel}</p>
                    <p className={cn("mt-0.5 text-xs font-medium", accent ?? "text-zinc-200")}>{value}</p>
                  </div>
                ))}
              </div>

              {/* ── VIOLATION FLAGS ── */}
              <ViolationChips
                violations={detail.violations}
                riskContext={detail.risk_context}
              />

              <div className="flex justify-end border-t border-zinc-800 pt-3">
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  onClick={() =>
                    navigate(
                      `/identity-review?scope=active_session&item=${encodeURIComponent(`active:${person.person_id}`)}`,
                    )
                  }
                  className="bg-zinc-800 text-zinc-100 hover:bg-zinc-700"
                >
                  Review identity
                </Button>
              </div>

            </div>
          ) : (
            <p className="p-4 text-sm text-zinc-500">Could not load person details.</p>
          )}
        </div>
      )}

      {/* Lightbox */}
      {lightboxSrc && (
        <Lightbox
          src={lightboxSrc}
          alt={`Evidence for ${label}`}
          onClose={() => setLightboxSrc(null)}
        />
      )}
    </div>
  );
}


export default function DashboardPage() {
  const { user } = useAuth();
  useLiveOverview(user?.tenant_id ?? null);

  const { data: overview, isLoading: overviewLoading } = useOverviewToday();
  const acknowledge = useAcknowledgeAlert();

  const activeSession = overview?.session ?? null;
  const activeSessionId = activeSession?.id ?? null;

  useLiveEmployeeIntelligence(activeSessionId);
  useLiveAlerts(activeSessionId);

  const { data: allAlerts, isLoading: alertsLoading } = useAlerts({
    session_id: activeSessionId,
    limit: 10,
  });

  const { data: intelligence, isLoading: intelLoading } = useEmployeeIntelligence(activeSessionId);
  const { data: unknownData, isLoading: unknownLoading } = useUnknownPersons(activeSessionId);
  const unknownPersons = unknownData?.persons ?? [];
  const unknownSummary = unknownData?.summary;

  // Stable sort: capture person order on first load, append new arrivals at the end.
  // Never re-sort on polling refresh — prevents cards jumping around while the manager is reviewing.
  const stableOrderRef = useRef<string[]>([]);
  useEffect(() => {
    // Reset when session changes
    stableOrderRef.current = [];
  }, [activeSessionId]);
  useEffect(() => {
    if (!unknownPersons.length) return;
    const existing = new Set(stableOrderRef.current);
    const newIds = unknownPersons.map((p) => p.person_id).filter((id) => !existing.has(id));
    if (newIds.length > 0) {
      stableOrderRef.current = [...stableOrderRef.current, ...newIds];
    }
  }, [unknownPersons]);
  const sortedUnknownPersons = useMemo(() => {
    if (!stableOrderRef.current.length) return unknownPersons;
    const byId = new Map(unknownPersons.map((p) => [p.person_id, p]));
    return stableOrderRef.current.filter((id) => byId.has(id)).map((id) => byId.get(id)!);
  }, [unknownPersons]);

  const onsite = overview?.summary.counts.present ?? 0;
  const unknownCount = unknownSummary?.unknown_persons ?? overview?.summary.counts.unknown ?? unknownPersons.length;
  const alertCount = allAlerts?.length ?? 0;
  const camerasOnline = overview?.summary.health.camera_active ?? 0;
  const isRunning = activeSession?.status === "running";
  const dashboardInsights = buildDashboardInsights(intelligence, allAlerts);

  const handleAcknowledge = (id: string) => {
    acknowledge.mutate(id, {
      onSuccess: () => toast.success("Alert acknowledged"),
      onError: () => toast.error("Failed to acknowledge alert"),
    });
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <PageHeader
          title="Dashboard"
          description={
            activeSession
              ? `${activeSession.name} · ${isRunning ? "Running" : activeSession.status}`
              : "Live workforce overview"
          }
        />
      </div>

      <div className="grid grid-cols-2 gap-4 xl:grid-cols-5">
        <KpiCard title="Onsite Now" value={onsite} icon={Users} accent="emerald" loading={overviewLoading} />
        <KpiCard
          title="Session Active"
          value={isRunning ? 1 : 0}
          icon={Play}
          accent="sky"
          loading={overviewLoading}
        />
        <KpiCard
          title="Recent Alerts"
          value={alertCount}
          icon={Bell}
          accent={alertCount > 0 ? "red" : "sky"}
          loading={alertsLoading}
        />
        <KpiCard
          title="Cameras Online"
          value={camerasOnline}
          icon={Camera}
          accent="violet"
          loading={overviewLoading}
        />
        <KpiCard
          title="Unknown Persons"
          value={unknownCount}
          icon={Eye}
          accent={unknownCount > 0 ? "red" : "sky"}
          loading={overviewLoading || unknownLoading}
        />
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        <div className="rounded-xl border border-zinc-800 bg-zinc-900 lg:col-span-2">
          <div className="border-b border-zinc-800 px-5 py-4">
            <h2 className="text-sm font-medium text-zinc-100">Employee Intelligence</h2>
            {activeSessionId && <p className="mt-0.5 text-xs text-zinc-500">Auto-refreshes every 30s</p>}
          </div>
          <div className="overflow-x-auto">
            {!activeSessionId ? (
              <div className="flex flex-col items-center justify-center py-16 text-center">
                <Play className="mb-3 h-8 w-8 text-zinc-700" />
                <p className="text-sm text-zinc-500">No active session</p>
                <p className="mt-1 text-xs text-zinc-600">Start a session to see live employee data</p>
              </div>
            ) : intelLoading ? (
              <div className="space-y-3 p-4">
                {Array.from({ length: 5 }).map((_, i) => (
                  <Skeleton key={i} className="h-10 w-full bg-zinc-800" />
                ))}
              </div>
            ) : !intelligence?.length ? (
              <div className="flex items-center justify-center py-16">
                <p className="text-sm text-zinc-500">No employees detected in this session</p>
              </div>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-zinc-800">
                    <th className="px-5 py-3 text-left text-xs font-medium uppercase tracking-wide text-zinc-500">
                      Name
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wide text-zinc-500">
                      Status
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wide text-zinc-500">
                      Location
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wide text-zinc-500">
                      Productivity
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wide text-zinc-500">
                      Violations
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {intelligence.map((emp: EmployeeIntelligence, index) => (
                    <tr
                      key={emp.employee_id ?? `employee-intelligence-${index}`}
                      className="border-b border-zinc-800/50 transition-colors hover:bg-zinc-800/30"
                    >
                      <td className="px-5 py-3">
                        <div className="flex items-center gap-2.5">
                          <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-zinc-700 text-xs font-medium text-zinc-300">
                            {emp.employee_name?.charAt(0)?.toUpperCase()}
                          </div>
                          <div>
                            <p className="font-medium text-zinc-100">{emp.employee_name}</p>
                            {emp.training_status === "untrained" && (
                              <p className="text-xs text-amber-400">Untrained</p>
                            )}
                          </div>
                        </div>
                      </td>
                      <td className="px-4 py-3">
                        {emp.presence.is_present ? (
                          <StatusBadge status={emp.live_status === "unknown" ? "idle" : emp.live_status} />
                        ) : (
                          <StatusBadge status="offline" label="Absent" />
                        )}
                      </td>
                      <td className="px-4 py-3 text-xs text-zinc-400">{emp.location.current_zone ?? "—"}</td>
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-2">
                          <div className="h-1.5 w-20 rounded-full bg-zinc-800">
                            <div
                              className="h-1.5 rounded-full bg-sky-500"
                              style={{ width: `${emp.productivity.productivity_percent}%` }}
                            />
                          </div>
                          <span className="text-xs text-zinc-400">{emp.productivity.productivity_percent}%</span>
                        </div>
                      </td>
                      <td className="px-4 py-3">
                        {emp.violations.phone_violation || emp.violations.restricted_zone_violation ? (
                          <div className="flex items-center gap-1.5 text-xs text-red-400">
                            <AlertTriangle className="h-3.5 w-3.5" />
                            {emp.violations.phone_violation ? "Phone" : "Zone"}
                          </div>
                        ) : (
                          <div className="flex items-center gap-1.5 text-xs text-zinc-600">
                            <CheckCircle2 className="h-3.5 w-3.5" />
                            None
                          </div>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>

        <div className="rounded-xl border border-zinc-800 bg-zinc-900">
          <div className="border-b border-zinc-800 px-5 py-4">
            <h2 className="text-sm font-medium text-zinc-100">Recent Alerts</h2>
          </div>
          <div className="divide-y divide-zinc-800">
            {!activeSessionId ? (
              <div className="flex items-center justify-center py-12">
                <p className="text-sm text-zinc-500">No active session</p>
              </div>
            ) : alertsLoading ? (
              <div className="space-y-3 p-4">
                {Array.from({ length: 5 }).map((_, i) => (
                  <Skeleton key={i} className="h-12 w-full bg-zinc-800" />
                ))}
              </div>
            ) : !allAlerts?.length ? (
              <div className="flex items-center justify-center py-12">
                <p className="text-sm text-zinc-500">No alerts</p>
              </div>
            ) : (
              allAlerts.map((alert) => (
                <div key={alert.id} className="flex items-start gap-3 px-4 py-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <StatusBadge status={alert.severity} />
                      <span className="text-xs text-zinc-500">{formatRelative(alert.created_at)}</span>
                    </div>
                    <p className="mt-1 line-clamp-2 text-xs text-zinc-300">{alert.message}</p>
                    {alert.camera_name && <p className="mt-0.5 text-xs text-zinc-600">{alert.camera_name}</p>}
                  </div>
                  {!alert.acknowledged && (
                    <Button
                      size="sm"
                      variant="ghost"
                      className="h-6 shrink-0 px-2 text-xs text-sky-400 hover:bg-sky-500/10 hover:text-sky-300"
                      onClick={() => handleAcknowledge(alert.id)}
                      disabled={acknowledge.isPending}
                    >
                      Ack
                    </Button>
                  )}
                </div>
              ))
            )}
          </div>
        </div>
      </div>

      <Card className="border-zinc-800 bg-zinc-900">
        <CardHeader className="border-b border-zinc-800 px-5 py-4">
          <div className="flex items-center gap-3">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-sky-500/10 text-sky-400">
              <Sparkles className="h-4 w-4" />
            </div>
            <div>
              <CardTitle className="text-sm font-medium text-zinc-100">AI Smart Insights</CardTitle>
              <p className="text-xs text-zinc-500">Computed from live overview, employee-intelligence, and alerts data</p>
            </div>
          </div>
        </CardHeader>
        <CardContent className="px-5 py-4">
          {!activeSessionId ? (
            <div className="flex items-center justify-center py-10">
              <p className="text-sm text-zinc-500">Start a session to generate AI insights</p>
            </div>
          ) : intelLoading || alertsLoading ? (
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              {Array.from({ length: 5 }).map((_, index) => (
                <Skeleton key={index} className="h-16 bg-zinc-800" />
              ))}
            </div>
          ) : (
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              {dashboardInsights.map((insight) => (
                <InsightCard key={insight.id} insight={insight} />
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      <Card className="border-zinc-800 bg-zinc-900">
        <CardHeader className="border-b border-zinc-800 px-5 py-4">
          <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <div className="flex items-center gap-2">
                <ShieldAlert className="h-4 w-4 text-red-400" />
                <CardTitle className="text-sm font-medium text-zinc-100">Unknown Person Activity</CardTitle>
                <span className="rounded-full bg-red-500/10 px-2 py-0.5 text-xs font-medium text-red-400">
                  {unknownCount} Unidentified
                </span>
              </div>
              <p className="mt-1 text-xs text-zinc-500">Real-time tracking for anonymous session persons</p>
            </div>
          </div>
        </CardHeader>
        <CardContent className="p-0">
          {!activeSessionId ? (
            <div className="flex items-center justify-center py-12">
              <p className="text-sm text-zinc-500">Start a session to track unknown persons</p>
            </div>
          ) : unknownLoading ? (
            <div className="space-y-3 p-4">
              {Array.from({ length: 4 }).map((_, index) => (
                <Skeleton key={index} className="h-12 w-full bg-zinc-800" />
              ))}
            </div>
          ) : !unknownPersons.length ? (
            <div className="flex flex-col items-center justify-center py-12 text-center">
              <ShieldAlert className="mb-3 h-7 w-7 text-zinc-700" />
              <p className="text-sm text-zinc-400">No unknown people tracked</p>
              <p className="mt-1 text-xs text-zinc-600">Anonymous detections will appear here as soon as the pipeline emits them.</p>
            </div>
          ) : (
            <div className="space-y-4 p-4">
              <div className="grid gap-3 md:grid-cols-4">
                <div className="rounded-xl border border-zinc-800 bg-zinc-950 px-4 py-3">
                  <p className="text-[11px] uppercase tracking-wide text-zinc-500">Tracked</p>
                  <p className="mt-1 text-lg font-semibold text-zinc-100">{unknownSummary?.active_unknown_persons ?? 0}</p>
                </div>
                <div className="rounded-xl border border-zinc-800 bg-zinc-950 px-4 py-3">
                  <p className="text-[11px] uppercase tracking-wide text-zinc-500">Active Alerts</p>
                  <p className="mt-1 text-lg font-semibold text-zinc-100">{unknownSummary?.active_alerts ?? 0}</p>
                </div>
                <div className="rounded-xl border border-zinc-800 bg-zinc-950 px-4 py-3">
                  <p className="text-[11px] uppercase tracking-wide text-zinc-500">High Risk</p>
                  <p className="mt-1 text-lg font-semibold text-zinc-100">{unknownSummary?.high_risk_persons ?? 0}</p>
                </div>
                <div className="rounded-xl border border-zinc-800 bg-zinc-950 px-4 py-3">
                  <p className="text-[11px] uppercase tracking-wide text-zinc-500">Avg Confidence</p>
                  <p className="mt-1 text-lg font-semibold text-zinc-100">
                    {Math.round((unknownSummary?.average_face_confidence ?? 0) * 100)}%
                  </p>
                </div>
              </div>
              <div className="space-y-3">
              {sortedUnknownPersons.map((person) => (
                <UnknownPersonCard
                  key={person.person_id}
                  person={person}
                  sessionId={activeSessionId}
                />
              ))}
              </div>
              <div className="border-t border-zinc-800 px-1 pt-3 text-xs text-zinc-500">
                Showing {unknownPersons.length} canonical unknown person{unknownPersons.length === 1 ? "" : "s"}
              </div>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
