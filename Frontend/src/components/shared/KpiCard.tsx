import type { LucideIcon } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

interface KpiCardProps {
  title: string;
  value: string | number;
  icon: LucideIcon;
  accent?: "sky" | "emerald" | "amber" | "red" | "violet";
  loading?: boolean;
  className?: string;
}

const accentBorder: Record<string, string> = {
  sky: "border-l-sky-500",
  emerald: "border-l-emerald-500",
  amber: "border-l-amber-500",
  red: "border-l-red-500",
  violet: "border-l-violet-500",
};

const accentIcon: Record<string, string> = {
  sky: "text-sky-400",
  emerald: "text-emerald-400",
  amber: "text-amber-400",
  red: "text-red-400",
  violet: "text-violet-400",
};

export function KpiCard({ title, value, icon: Icon, accent = "sky", loading, className }: KpiCardProps) {
  return (
    <div
      className={cn(
        "rounded-xl border border-zinc-800 border-l-2 bg-zinc-900 p-5",
        accentBorder[accent],
        className,
      )}
    >
      <div className="flex items-center justify-between">
        <p className="text-sm text-zinc-400">{title}</p>
        <Icon className={cn("h-4 w-4", accentIcon[accent])} />
      </div>
      {loading ? (
        <Skeleton className="mt-2 h-8 w-20 bg-zinc-800" />
      ) : (
        <p className="mt-2 text-3xl font-bold text-zinc-50">{value}</p>
      )}
    </div>
  );
}
