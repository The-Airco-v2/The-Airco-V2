import {
  Activity,
  AlertTriangle,
  CalendarDays,
  Clock3,
  History,
  LineChart,
  Trophy,
  UserCheck,
  Users,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useState } from "react";
import { KpiCard } from "@/components/shared/KpiCard";
import { PageHeader } from "@/components/shared/PageHeader";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  useReportDaySummary,
  useReportEmployeeAnalysis,
  useReportLeaderboards,
  useReportMonthlyTimeline,
  useReportTodayAttendanceLog,
  useReportTodayInsights,
  useReportTodaySummary,
} from "@/hooks/useReports";
import { cn } from "@/lib/utils";

function formatTimestamp(iso: string | null) {
  if (!iso) {
    return "—";
  }
  return new Date(iso).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatWorkMinutes(totalMinutes: number) {
  if (!totalMinutes || totalMinutes <= 0) {
    return "—";
  }
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  return hours > 0 ? `${hours}h ${minutes}m` : `${minutes}m`;
}

function statusClasses(status: string) {
  switch (status) {
    case "on_time":
      return "bg-emerald-500/10 text-emerald-400";
    case "late":
      return "bg-amber-500/10 text-amber-400";
    case "absent":
      return "bg-red-500/10 text-red-400";
    default:
      return "bg-zinc-800 text-zinc-400";
  }
}

function statusLabel(status: string) {
  switch (status) {
    case "on_time":
      return "On time";
    case "late":
      return "Late";
    case "absent":
      return "Absent";
    default:
      return status;
  }
}

function ReportMiniList<TItem extends { employee_name: string; department: string | null }>({
  title,
  emptyLabel,
  items,
  renderValue,
  icon: Icon,
  tone = "sky",
  loading,
}: {
  title: string;
  emptyLabel: string;
  items: TItem[];
  renderValue: (item: TItem) => string;
  icon: LucideIcon;
  tone?: "sky" | "emerald" | "amber" | "red";
  loading?: boolean;
}) {
  const accentClasses = {
    sky: "border-sky-500/30 bg-sky-500/5 text-sky-400",
    emerald: "border-emerald-500/30 bg-emerald-500/5 text-emerald-400",
    amber: "border-amber-500/30 bg-amber-500/5 text-amber-400",
    red: "border-red-500/30 bg-red-500/5 text-red-400",
  }[tone];

  return (
    <Card className="border-zinc-800 bg-zinc-900">
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-sm text-zinc-100">
          <span className={cn("rounded-md border p-1.5", accentClasses)}>
            <Icon className="h-4 w-4" />
          </span>
          {title}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        {loading ? (
          <>
            <Skeleton className="h-12 bg-zinc-800" />
            <Skeleton className="h-12 bg-zinc-800" />
            <Skeleton className="h-12 bg-zinc-800" />
          </>
        ) : items.length === 0 ? (
          <p className="rounded-lg border border-dashed border-zinc-800 px-3 py-6 text-center text-sm text-zinc-500">
            {emptyLabel}
          </p>
        ) : (
          items.slice(0, 5).map((item) => (
            <div
              key={`${item.employee_name}-${renderValue(item)}`}
              className="flex items-center justify-between rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2.5"
            >
              <div>
                <p className="text-sm font-medium text-zinc-100">{item.employee_name}</p>
                <p className="text-xs text-zinc-500">{item.department ?? "—"}</p>
              </div>
              <span className="text-xs font-medium text-zinc-300">{renderValue(item)}</span>
            </div>
          ))
        )}
      </CardContent>
    </Card>
  );
}

export default function ReportsPage() {
  const [selectedDate, setSelectedDate] = useState<string | null>(null);

  const todaySummary = useReportTodaySummary();
  const todayInsights = useReportTodayInsights();
  const todayAttendanceLog = useReportTodayAttendanceLog();
  const monthlyTimeline = useReportMonthlyTimeline(30);
  const employeeAnalysis = useReportEmployeeAnalysis(30);
  const leaderboards = useReportLeaderboards(30);

  useEffect(() => {
    if (!selectedDate && monthlyTimeline.data?.days.length) {
      setSelectedDate(monthlyTimeline.data.days[0]?.date ?? null);
    }
  }, [monthlyTimeline.data, selectedDate]);

  const selectedHistorical = useReportDaySummary(selectedDate);

  return (
    <div className="space-y-8">
      <PageHeader
        title="Reports"
        description="Daily operations reporting and historical workforce analytics from real V2 session data."
      />

      <section className="space-y-4">
        <div className="flex items-center gap-2">
          <CalendarDays className="h-4 w-4 text-sky-400" />
          <h2 className="text-sm font-semibold uppercase tracking-[0.18em] text-zinc-400">
            Today&apos;s Report
          </h2>
        </div>

        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-6">
          <KpiCard
            title="Present"
            value={todaySummary.data?.cards.present_today ?? 0}
            icon={Users}
            accent="emerald"
            loading={todaySummary.isLoading}
          />
          <KpiCard
            title="Absent"
            value={todaySummary.data?.cards.absent_today ?? 0}
            icon={AlertTriangle}
            accent="red"
            loading={todaySummary.isLoading}
          />
          <KpiCard
            title="Late Arrivals"
            value={todaySummary.data?.cards.late_arrivals ?? 0}
            icon={Clock3}
            accent="amber"
            loading={todaySummary.isLoading}
          />
          <KpiCard
            title="Avg Productivity"
            value={`${todaySummary.data?.cards.avg_productivity ?? 0}%`}
            icon={Activity}
            accent="sky"
            loading={todaySummary.isLoading}
          />
          <KpiCard
            title="Active Cameras"
            value={`${todaySummary.data?.cards.active_cameras.online ?? 0}/${todaySummary.data?.cards.active_cameras.total ?? 0}`}
            icon={LineChart}
            accent="violet"
            loading={todaySummary.isLoading}
          />
          <KpiCard
            title="Footfall"
            value={todaySummary.data?.cards.footfall_today ?? 0}
            icon={UserCheck}
            accent="sky"
            loading={todaySummary.isLoading}
          />
        </div>

        <div className="grid gap-4 xl:grid-cols-4">
          <ReportMiniList
            title="Who Was Late Today?"
            emptyLabel="No late arrivals today."
            items={todayInsights.data?.cards.late_arrivals ?? []}
            renderValue={(item) => formatTimestamp("first_entry" in item ? item.first_entry : null)}
            icon={Clock3}
            tone="amber"
            loading={todayInsights.isLoading}
          />
          <ReportMiniList
            title="Who Was On Time Today?"
            emptyLabel="No on-time arrivals recorded yet."
            items={todayInsights.data?.cards.on_time ?? []}
            renderValue={(item) => formatTimestamp("first_entry" in item ? item.first_entry : null)}
            icon={UserCheck}
            tone="emerald"
            loading={todayInsights.isLoading}
          />
          <ReportMiniList
            title="Phone Usage Today"
            emptyLabel="No phone usage detected today."
            items={todayInsights.data?.cards.phone_usage ?? []}
            renderValue={(item) =>
              `${"phone_usage_minutes" in item ? item.phone_usage_minutes.toFixed(1) : "0.0"} min`
            }
            icon={Activity}
            tone="sky"
            loading={todayInsights.isLoading}
          />
          <ReportMiniList
            title="Today&apos;s Violations"
            emptyLabel="No violations today."
            items={todayInsights.data?.cards.violations ?? []}
            renderValue={(item) => `${"violations" in item ? item.violations : 0} total`}
            icon={AlertTriangle}
            tone="red"
            loading={todayInsights.isLoading}
          />
        </div>

        <Card className="border-zinc-800 bg-zinc-900">
          <CardHeader>
            <CardTitle className="text-sm text-zinc-100">Today&apos;s Complete Attendance Log</CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            {todayAttendanceLog.isLoading ? (
              <div className="space-y-3 p-6">
                <Skeleton className="h-10 bg-zinc-800" />
                <Skeleton className="h-10 bg-zinc-800" />
                <Skeleton className="h-10 bg-zinc-800" />
              </div>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow className="border-zinc-800 hover:bg-transparent">
                    <TableHead>Employee</TableHead>
                    <TableHead>Department</TableHead>
                    <TableHead>First Entry</TableHead>
                    <TableHead>Last Seen</TableHead>
                    <TableHead>Work Time</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Violations</TableHead>
                    <TableHead>Productivity</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {todayAttendanceLog.data?.rows.map((row) => (
                    <TableRow key={row.employee_id} className="border-zinc-800">
                      <TableCell className="font-medium text-zinc-100">{row.employee_name}</TableCell>
                      <TableCell className="text-zinc-400">{row.department ?? "—"}</TableCell>
                      <TableCell>{formatTimestamp(row.first_entry)}</TableCell>
                      <TableCell>{formatTimestamp(row.last_seen)}</TableCell>
                      <TableCell>{formatWorkMinutes(row.total_work_minutes)}</TableCell>
                      <TableCell>
                        <span className={cn("rounded-full px-2 py-1 text-xs font-medium", statusClasses(row.status))}>
                          {statusLabel(row.status)}
                        </span>
                      </TableCell>
                      <TableCell>{row.violations}</TableCell>
                      <TableCell>{row.productivity_percent}%</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      </section>

      <section className="space-y-4">
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-2">
            <History className="h-4 w-4 text-violet-400" />
            <h2 className="text-sm font-semibold uppercase tracking-[0.18em] text-zinc-400">
              Historical Insights
            </h2>
          </div>

          <select
            value={selectedDate ?? ""}
            onChange={(event) => setSelectedDate(event.target.value)}
            className="rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 outline-none"
          >
            {(monthlyTimeline.data?.days ?? []).map((day) => (
              <option key={day.date} value={day.date}>
                {day.label}
              </option>
            ))}
          </select>
        </div>

        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-7">
          <KpiCard
            title="Present"
            value={selectedHistorical.data?.summary.present ?? 0}
            icon={Users}
            accent="emerald"
            loading={selectedHistorical.isLoading}
          />
          <KpiCard
            title="Absent"
            value={selectedHistorical.data?.summary.absent ?? 0}
            icon={AlertTriangle}
            accent="red"
            loading={selectedHistorical.isLoading}
          />
          <KpiCard
            title="Late"
            value={selectedHistorical.data?.summary.late_arrivals ?? 0}
            icon={Clock3}
            accent="amber"
            loading={selectedHistorical.isLoading}
          />
          <KpiCard
            title="Productivity"
            value={`${selectedHistorical.data?.summary.avg_productivity ?? 0}%`}
            icon={Activity}
            accent="sky"
            loading={selectedHistorical.isLoading}
          />
          <KpiCard
            title="Cameras"
            value={selectedHistorical.data?.summary.active_cameras ?? 0}
            icon={LineChart}
            accent="violet"
            loading={selectedHistorical.isLoading}
          />
          <KpiCard
            title="Footfall"
            value={selectedHistorical.data?.summary.footfall ?? 0}
            icon={UserCheck}
            accent="sky"
            loading={selectedHistorical.isLoading}
          />
          <KpiCard
            title="Violations"
            value={selectedHistorical.data?.summary.violations ?? 0}
            icon={AlertTriangle}
            accent="red"
            loading={selectedHistorical.isLoading}
          />
        </div>

        <Card className="border-zinc-800 bg-zinc-900">
          <CardHeader>
            <CardTitle className="text-sm text-zinc-100">
              Historical Attendance Snapshot
            </CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            {selectedHistorical.isLoading ? (
              <div className="space-y-3 p-6">
                <Skeleton className="h-10 bg-zinc-800" />
                <Skeleton className="h-10 bg-zinc-800" />
              </div>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow className="border-zinc-800 hover:bg-transparent">
                    <TableHead>Employee</TableHead>
                    <TableHead>First Entry</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Work Time</TableHead>
                    <TableHead>Productivity</TableHead>
                    <TableHead>Violations</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {selectedHistorical.data?.employees.map((row) => (
                    <TableRow key={`${selectedHistorical.data?.date}-${row.employee_id}`} className="border-zinc-800">
                      <TableCell className="font-medium text-zinc-100">{row.employee_name}</TableCell>
                      <TableCell>{formatTimestamp(row.first_entry)}</TableCell>
                      <TableCell>
                        <span className={cn("rounded-full px-2 py-1 text-xs font-medium", statusClasses(row.status))}>
                          {statusLabel(row.status)}
                        </span>
                      </TableCell>
                      <TableCell>{formatWorkMinutes(row.total_work_minutes)}</TableCell>
                      <TableCell>{row.productivity_percent}%</TableCell>
                      <TableCell>{row.violations}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>

        <div className="grid gap-4 xl:grid-cols-[1.2fr_1fr]">
          <Card className="border-zinc-800 bg-zinc-900">
            <CardHeader>
              <CardTitle className="text-sm text-zinc-100">Monthly Timeline</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              {monthlyTimeline.isLoading ? (
                <>
                  <Skeleton className="h-16 bg-zinc-800" />
                  <Skeleton className="h-16 bg-zinc-800" />
                  <Skeleton className="h-16 bg-zinc-800" />
                </>
              ) : (
                monthlyTimeline.data?.days.map((day) => (
                  <button
                    key={day.date}
                    type="button"
                    onClick={() => setSelectedDate(day.date)}
                    className={cn(
                      "flex w-full items-center justify-between rounded-xl border px-4 py-3 text-left transition-colors",
                      selectedDate === day.date
                        ? "border-sky-500/40 bg-sky-500/5"
                        : "border-zinc-800 bg-zinc-950 hover:border-zinc-700",
                    )}
                  >
                    <div>
                      <p className="text-sm font-medium text-zinc-100">{day.label}</p>
                      <p className="mt-1 text-xs text-zinc-500">{day.date}</p>
                    </div>
                    <div className="flex gap-4 text-xs">
                      <span className="text-emerald-400">{day.summary.present} present</span>
                      <span className="text-amber-400">{day.summary.late_arrivals} late</span>
                      <span className="text-red-400">{day.summary.violations} violations</span>
                      <span className="text-sky-400">{day.summary.avg_productivity}% productivity</span>
                    </div>
                  </button>
                ))
              )}
            </CardContent>
          </Card>

          <Card className="border-zinc-800 bg-zinc-900">
            <CardHeader>
              <CardTitle className="text-sm text-zinc-100">Per Employee Analysis</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              {employeeAnalysis.isLoading ? (
                <>
                  <Skeleton className="h-14 bg-zinc-800" />
                  <Skeleton className="h-14 bg-zinc-800" />
                  <Skeleton className="h-14 bg-zinc-800" />
                </>
              ) : (
                employeeAnalysis.data?.employees.slice(0, 8).map((employee) => (
                  <div
                    key={employee.employee_id}
                    className="flex items-center justify-between rounded-xl border border-zinc-800 bg-zinc-950 px-4 py-3"
                  >
                    <div>
                      <p className="text-sm font-medium text-zinc-100">{employee.employee_name}</p>
                      <p className="mt-1 text-xs text-zinc-500">{employee.department ?? "—"}</p>
                    </div>
                    <div className="flex gap-4 text-xs">
                      <span className="text-sky-400">{employee.avg_productivity_percent}% avg</span>
                      <span className="text-amber-400">{employee.late_count} late</span>
                      <span className="text-violet-400">{employee.avg_work_hours.toFixed(1)}h avg</span>
                    </div>
                  </div>
                ))
              )}
            </CardContent>
          </Card>
        </div>

        <div className="grid gap-4 xl:grid-cols-4">
          <ReportMiniList
            title="Top Performers"
            emptyLabel="No performer data yet."
            items={(leaderboards.data?.performers ?? []).map((item) => ({
              employee_name: item.employee_name,
              department: null,
            }))}
            renderValue={(item) => {
              const row = (leaderboards.data?.performers ?? []).find(
                (candidate) => candidate.employee_name === item.employee_name,
              );
              return row ? `${row.score}%` : "—";
            }}
            icon={Trophy}
            tone="amber"
            loading={leaderboards.isLoading}
          />
          <ReportMiniList
            title="Best Attendance"
            emptyLabel="No attendance data yet."
            items={(leaderboards.data?.attendance ?? []).map((item) => ({
              employee_name: item.employee_name,
              department: null,
            }))}
            renderValue={(item) => {
              const row = (leaderboards.data?.attendance ?? []).find(
                (candidate) => candidate.employee_name === item.employee_name,
              );
              return row ? `${row.attendance_percent}%` : "—";
            }}
            icon={UserCheck}
            tone="emerald"
            loading={leaderboards.isLoading}
          />
          <ReportMiniList
            title="Late Behavior"
            emptyLabel="No late history yet."
            items={(leaderboards.data?.late_behavior ?? []).map((item) => ({
              employee_name: item.employee_name,
              department: null,
            }))}
            renderValue={(item) => {
              const row = (leaderboards.data?.late_behavior ?? []).find(
                (candidate) => candidate.employee_name === item.employee_name,
              );
              return row ? `${row.late_count} times` : "—";
            }}
            icon={Clock3}
            tone="amber"
            loading={leaderboards.isLoading}
          />
          <ReportMiniList
            title="Low Work Hours"
            emptyLabel="No work-hour data yet."
            items={(leaderboards.data?.low_work_hours ?? []).map((item) => ({
              employee_name: item.employee_name,
              department: null,
            }))}
            renderValue={(item) => {
              const row = (leaderboards.data?.low_work_hours ?? []).find(
                (candidate) => candidate.employee_name === item.employee_name,
              );
              return row ? `${row.avg_work_hours.toFixed(1)}h` : "—";
            }}
            icon={History}
            tone="red"
            loading={leaderboards.isLoading}
          />
        </div>
      </section>
    </div>
  );
}
