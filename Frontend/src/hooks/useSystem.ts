import { useQuery } from "@tanstack/react-query";
import { apiFetchJson } from "@/lib/api";
import type {
  FaceTrainingMetricsResponse,
  FaceTrainingPreviewResponse,
  SystemHealthResponse,
} from "@/types";

export function useSystemHealth() {
  return useQuery({
    queryKey: ["system-health"],
    queryFn: () => apiFetchJson<SystemHealthResponse>("/api/v2/system/health"),
    refetchInterval: 5000,
  });
}

export function useFaceTrainingMetrics() {
  return useQuery({
    queryKey: ["face-training-metrics"],
    queryFn: () => apiFetchJson<FaceTrainingMetricsResponse>("/api/v2/face-training/metrics"),
    refetchInterval: 5000,
  });
}

export function useFaceTrainingPreview(jobId: string | null) {
  return useQuery({
    queryKey: ["face-training-preview", jobId],
    enabled: !!jobId,
    queryFn: () => apiFetchJson<FaceTrainingPreviewResponse>(`/api/v2/face-training/${jobId}/preview`),
    refetchInterval: jobId ? 2000 : false,
  });
}
