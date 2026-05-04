import { useQuery } from "@tanstack/react-query";
import { apiFetchJson } from "@/lib/api";
import type {
  ReportDaySummary,
  ReportEmployeeAnalysis,
  ReportLeaderboards,
  ReportMonthlyTimeline,
  ReportTodayAttendanceLog,
  ReportTodayInsights,
  ReportTodaySummary,
} from "@/types";

export function useReportTodaySummary() {
  return useQuery({
    queryKey: ["reports", "today-summary"],
    queryFn: () => apiFetchJson<ReportTodaySummary>("/api/v2/reports/today-summary"),
    refetchInterval: 60_000,
  });
}

export function useReportTodayInsights() {
  return useQuery({
    queryKey: ["reports", "today-insights"],
    queryFn: () => apiFetchJson<ReportTodayInsights>("/api/v2/reports/today-insights"),
    refetchInterval: 60_000,
  });
}

export function useReportTodayAttendanceLog() {
  return useQuery({
    queryKey: ["reports", "today-attendance-log"],
    queryFn: () => apiFetchJson<ReportTodayAttendanceLog>("/api/v2/reports/today-attendance-log"),
    refetchInterval: 60_000,
  });
}

export function useReportMonthlyTimeline(days = 30) {
  return useQuery({
    queryKey: ["reports", "monthly-timeline", days],
    queryFn: () =>
      apiFetchJson<ReportMonthlyTimeline>(`/api/v2/reports/monthly-timeline?days=${days}`),
  });
}

export function useReportDaySummary(date: string | null) {
  return useQuery({
    queryKey: ["reports", "day-summary", date],
    queryFn: () => apiFetchJson<ReportDaySummary>(`/api/v2/reports/day-summary?date=${date}`),
    enabled: !!date,
  });
}

export function useReportEmployeeAnalysis(days = 30) {
  return useQuery({
    queryKey: ["reports", "employee-analysis", days],
    queryFn: () =>
      apiFetchJson<ReportEmployeeAnalysis>(`/api/v2/reports/employee-analysis?days=${days}`),
  });
}

export function useReportLeaderboards(days = 30) {
  return useQuery({
    queryKey: ["reports", "leaderboards", days],
    queryFn: () => apiFetchJson<ReportLeaderboards>(`/api/v2/reports/leaderboards?days=${days}`),
  });
}
