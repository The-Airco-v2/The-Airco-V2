import { cn } from "@/lib/utils";

type Status =
  | "online"
  | "offline"
  | "active"
  | "stopped"
  | "running"
  | "trained"
  | "untrained"
  | "working"
  | "idle"
  | "walking"
  | "paused"
  | "critical"
  | "high"
  | "medium"
  | "low"
  | "unknown";

const statusConfig: Record<Status, { label: string; classes: string }> = {
  online: { label: "Online", classes: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20" },
  active: { label: "Active", classes: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20" },
  running: { label: "Running", classes: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20" },
  trained: { label: "Trained", classes: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20" },
  working: { label: "Working", classes: "bg-sky-500/10 text-sky-400 border-sky-500/20" },
  walking: { label: "Walking", classes: "bg-blue-500/10 text-blue-400 border-blue-500/20" },
  idle: { label: "Idle", classes: "bg-amber-500/10 text-amber-400 border-amber-500/20" },
  untrained: { label: "Untrained", classes: "bg-amber-500/10 text-amber-400 border-amber-500/20" },
  medium: { label: "Medium", classes: "bg-amber-500/10 text-amber-400 border-amber-500/20" },
  low: { label: "Low", classes: "bg-zinc-500/10 text-zinc-400 border-zinc-500/20" },
  offline: { label: "Offline", classes: "bg-red-500/10 text-red-400 border-red-500/20" },
  stopped: { label: "Stopped", classes: "bg-zinc-500/10 text-zinc-400 border-zinc-500/20" },
  paused: { label: "Paused", classes: "bg-amber-500/10 text-amber-400 border-amber-500/20" },
  critical: { label: "Critical", classes: "bg-red-500/10 text-red-400 border-red-500/20" },
  high: { label: "High", classes: "bg-orange-500/10 text-orange-400 border-orange-500/20" },
  unknown: { label: "Unknown", classes: "bg-zinc-500/10 text-zinc-400 border-zinc-500/20" },
};

interface StatusBadgeProps {
  status: Status;
  label?: string;
  className?: string;
}

export function StatusBadge({ status, label, className }: StatusBadgeProps) {
  const config = statusConfig[status] ?? statusConfig.unknown;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium",
        config.classes,
        className,
      )}
    >
      <span className="h-1.5 w-1.5 rounded-full bg-current" />
      {label ?? config.label}
    </span>
  );
}
