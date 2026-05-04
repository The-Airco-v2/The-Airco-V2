import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetchJson } from "@/lib/api";
import type { Camera, CreateCameraPayload } from "@/types";

export function useCameras() {
  return useQuery({
    queryKey: ["cameras"],
    queryFn: () => apiFetchJson<Camera[]>("/api/v2/cameras"),
  });
}

export function useCreateCamera() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: CreateCameraPayload) =>
      apiFetchJson<Camera>("/api/v2/cameras", {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["cameras"] }),
  });
}

export function useDeleteCamera() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiFetchJson(`/api/v2/cameras/${id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["cameras"] }),
  });
}
