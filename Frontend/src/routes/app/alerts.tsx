import {
  AlertTriangle,
  Bell,
  CheckCheck,
  CheckCircle,
  ChevronDown,
  Clock,
  Camera,
  Image as ImageIcon,
  XCircle,
} from "lucide-react";
import { useState } from "react";
import { PageHeader } from "@/components/shared/PageHeader";
import { KpiCard } from "@/components/shared/KpiCard";
import { StatusBadge } from "@/components/shared/StatusBadge";
import { Alert as AlertUI, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { useAcknowledgeAlert, useAlerts } from "@/hooks/useAlerts";
import { useSessions } from "@/hooks/useSessions";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import type { Alert, AlertSeverity } from "@/types";

const SEVERITIES: AlertSeverity[] = ["critical", "high", "medium", "low"];

type Tab = "active" | "resolved";

function AlertCard({
  alert,
  expanded,
  onToggle,
  onAcknowledge,
  acknowledging,
}: {
  alert: Alert;
  expanded: boolean;
  onToggle: () => void;
  onAcknowledge: () => void;
  acknowledging: boolean;
}) {
  const imageUrl = alert.snapshot_url || alert.evidence_url;

  return (
    <div
      className={cn(
        "rounded-xl border bg-zinc-900 transition-all",
        alert.severity === "critical" && !alert.acknowledged
          ? "border-red-500/30"
          : "border-zinc-800",
        expanded && "ring-1 ring-zinc-700",
      )}
    >
      {/* Card header — always visible */}
      <button
        onClick={onToggle}
        className="w-full text-left p-4 flex items-start justify-between gap-4"
      >
        <div className="flex items-start gap-3 min-w-0">
          <div
            className={cn(
              "mt-0.5 flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-lg",
              alert.severity === "critical"
                ? "bg-red-500/10"
                : alert.severity === "high"
                  ? "bg-orange-500/10"
                  : alert.severity === "medium"
                    ? "bg-amber-500/10"
                    : "bg-zinc-500/10",
            )}
          >
            <AlertTriangle
              className={cn(
                "h-4 w-4",
                alert.severity === "critical"
                  ? "text-red-400"
                  : alert.severity === "high"
                    ? "text-orange-400"
                    : alert.severity === "medium"
                      ? "text-amber-400"
                      : "text-zinc-400",
              )}
            />
          </div>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2 mb-1">
              <span className="text-sm font-semibold text-zinc-100">{alert.type}</span>
              <StatusBadge status={alert.severity} />
              {!alert.acknowledged && (
                <span className="text-[10px] font-medium text-emerald-400 bg-emerald-500/10 border border-emerald-500/20 px-2 py-0.5 rounded-full">
                  Open
                </span>
              )}
              {alert.acknowledged && (
                <span className="text-[10px] font-medium text-zinc-500 bg-zinc-500/10 border border-zinc-500/20 px-2 py-0.5 rounded-full">
                  Resolved
                </span>
              )}
            </div>
            <p className="text-xs text-zinc-400 leading-relaxed line-clamp-2">{alert.message}</p>
            <div className="flex flex-wrap items-center gap-3 mt-2 text-[11px] text-zinc-500">
              <span className="flex items-center gap-1">
                <Clock className="h-3 w-3" />
                {new Date(alert.created_at).toLocaleString()}
              </span>
              {alert.camera_name && (
                <span className="flex items-center gap-1">
                  <Camera className="h-3 w-3" />
                  {alert.camera_name}
                </span>
              )}
              {imageUrl && (
                <span className="flex items-center gap-1 text-sky-500">
                  <ImageIcon className="h-3 w-3" />
                  Has evidence
                </span>
              )}
            </div>
          </div>
        </div>

        <div className="flex items-center gap-2 flex-shrink-0">
          <ChevronDown
            className={cn(
              "h-4 w-4 text-zinc-500 transition-transform",
              expanded && "rotate-180",
            )}
          />
        </div>
      </button>

      {/* Expanded section — image + actions */}
      {expanded && (
        <div className="border-t border-zinc-800 p-4 space-y-4">
          {/* Evidence image */}
          {imageUrl ? (
            <div className="relative overflow-hidden rounded-lg bg-zinc-950 border border-zinc-800">
              <img
                src={imageUrl}
                alt={`Evidence for ${alert.type}`}
                className="w-full max-h-[400px] object-contain"
                onError={(e) => {
                  (e.target as HTMLImageElement).style.display = "none";
                  (e.target as HTMLImageElement).nextElementSibling?.classList.remove("hidden");
                }}
              />
              <div className="hidden flex-col items-center justify-center py-12 text-zinc-600">
                <ImageIcon className="h-8 w-8 mb-2" />
                <p className="text-sm">Image could not be loaded</p>
              </div>
            </div>
          ) : (
            <div className="flex flex-col items-center justify-center py-8 rounded-lg bg-zinc-950 border border-zinc-800/50 text-zinc-600">
              <ImageIcon className="h-8 w-8 mb-2" />
              <p className="text-sm">No evidence image available</p>
            </div>
          )}

          {/* Actions */}
          {!alert.acknowledged && (
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                onClick={(e) => {
                  e.stopPropagation();
                  onAcknowledge();
                }}
                disabled={acknowledging}
                className="gap-1.5 bg-emerald-600 text-white hover:bg-emerald-500"
              >
                <CheckCircle className="h-3.5 w-3.5" />
                Resolve
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={(e) => {
                  e.stopPropagation();
                  onAcknowledge();
                }}
                disabled={acknowledging}
                className="gap-1.5 text-zinc-400 hover:text-zinc-300"
              >
                <XCircle className="h-3.5 w-3.5" />
                Ignore
              </Button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function AlertsPage() {
  const [tab, setTab] = useState<Tab>("active");
  const [severityFilter, setSeverityFilter] = useState<AlertSeverity | "all">("all");
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const { data: sessions } = useSessions();
  const runningSessions = sessions?.filter((s) => s.status === "running") ?? [];
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const activeSessionId = selectedSessionId ?? runningSessions[0]?.id ?? null;

  const { data: alerts, isLoading, error } = useAlerts({ session_id: activeSessionId });
  const acknowledge = useAcknowledgeAlert();

  const allOpen = alerts?.filter((a) => !a.acknowledged) ?? [];
  const allResolved = alerts?.filter((a) => a.acknowledged) ?? [];

  const displayed = (tab === "active" ? allOpen : allResolved).filter((a) => {
    if (severityFilter !== "all" && a.severity !== severityFilter) return false;
    return true;
  });

  const criticalCount = allOpen.filter((a) => a.severity === "critical").length;

  const handleAcknowledge = (id: string) => {
    acknowledge.mutate(id, {
      onSuccess: () => {
        toast.success("Alert resolved");
        setExpandedId(null);
      },
      onError: () => toast.error("Failed to resolve alert"),
    });
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title="Alerts & Tickets"
        description={
          activeSessionId
            ? `${allOpen.length} open alert${allOpen.length !== 1 ? "s" : ""}`
            : "Select a session to view alerts"
        }
      />

      {/* KPI Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <KpiCard
          title="Total Open"
          value={isLoading ? "..." : allOpen.length}
          icon={Bell}
          accent="sky"
          loading={isLoading}
        />
        <KpiCard
          title="Critical"
          value={isLoading ? "..." : criticalCount}
          icon={AlertTriangle}
          accent="red"
          loading={isLoading}
        />
        <KpiCard
          title="Resolved"
          value={isLoading ? "..." : allResolved.length}
          icon={CheckCircle}
          accent="emerald"
          loading={isLoading}
        />
      </div>

      {/* Filters row */}
      <div className="flex items-center gap-3">
        {sessions && sessions.length > 0 && (
          <Select
            value={activeSessionId ?? ""}
            onValueChange={(v) => setSelectedSessionId(v || null)}
          >
            <SelectTrigger className="w-52 border-zinc-700 bg-zinc-900 text-sm text-zinc-300">
              <SelectValue placeholder="Select session" />
            </SelectTrigger>
            <SelectContent className="border-zinc-700 bg-zinc-900">
              {sessions.map((s) => (
                <SelectItem key={s.id} value={s.id} className="text-zinc-300">
                  {s.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
        <Select value={severityFilter} onValueChange={(v) => setSeverityFilter(v as AlertSeverity | "all")}>
          <SelectTrigger className="w-36 border-zinc-700 bg-zinc-900 text-sm text-zinc-300">
            <SelectValue placeholder="Severity" />
          </SelectTrigger>
          <SelectContent className="border-zinc-700 bg-zinc-900">
            <SelectItem value="all" className="text-zinc-300">
              All severities
            </SelectItem>
            {SEVERITIES.map((s) => (
              <SelectItem key={s} value={s} className="capitalize text-zinc-300">
                {s}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {/* Tabs */}
      <div className="flex items-center gap-1 border-b border-zinc-800">
        {(["active", "resolved"] as Tab[]).map((t) => {
          const count = t === "active" ? allOpen.length : allResolved.length;
          return (
            <button
              key={t}
              onClick={() => {
                setTab(t);
                setExpandedId(null);
              }}
              className={cn(
                "px-4 py-2.5 text-sm font-medium capitalize border-b-2 transition-colors",
                tab === t
                  ? "border-sky-500 text-sky-400"
                  : "border-transparent text-zinc-500 hover:text-zinc-300",
              )}
            >
              {t === "active" ? "Active Alerts" : "Resolved"}
              <span
                className={cn(
                  "ml-2 text-xs rounded-full px-1.5 py-0.5",
                  tab === t ? "bg-sky-500/15 text-sky-400" : "bg-zinc-800 text-zinc-500",
                )}
              >
                {count}
              </span>
            </button>
          );
        })}
      </div>

      {/* Error */}
      {error && (
        <AlertUI variant="destructive" className="border-red-800 bg-red-950/40">
          <AlertDescription className="text-red-300">Failed to load alerts.</AlertDescription>
        </AlertUI>
      )}

      {/* Content */}
      {!activeSessionId ? (
        <div className="flex flex-col items-center justify-center py-16">
          <Bell className="mb-3 h-8 w-8 text-zinc-700" />
          <p className="text-sm text-zinc-500">Select a session to view alerts</p>
        </div>
      ) : isLoading ? (
        <div className="space-y-3">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="rounded-xl border border-zinc-800 bg-zinc-900 p-4">
              <div className="flex items-start gap-3">
                <Skeleton className="h-9 w-9 rounded-lg bg-zinc-800" />
                <div className="flex-1 space-y-2">
                  <Skeleton className="h-4 w-40 bg-zinc-800" />
                  <Skeleton className="h-3 w-64 bg-zinc-800" />
                  <Skeleton className="h-3 w-32 bg-zinc-800" />
                </div>
              </div>
            </div>
          ))}
        </div>
      ) : !displayed.length ? (
        <div className="flex flex-col items-center justify-center py-16">
          <Bell className="mb-3 h-8 w-8 text-zinc-700" />
          <p className="text-sm text-zinc-500">
            {tab === "active" ? "No open alerts" : "No resolved alerts"}
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {displayed.map((alert) => (
            <AlertCard
              key={alert.id}
              alert={alert}
              expanded={expandedId === alert.id}
              onToggle={() => setExpandedId(expandedId === alert.id ? null : alert.id)}
              onAcknowledge={() => handleAcknowledge(alert.id)}
              acknowledging={acknowledge.isPending}
            />
          ))}
        </div>
      )}
    </div>
  );
}
