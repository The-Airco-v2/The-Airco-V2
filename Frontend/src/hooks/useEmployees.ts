import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetchJson } from "@/lib/api";
import type {
  CreateEmployeePayload,
  Employee,
  FaceTrainingCancelResponse,
  FaceTrainingStartPayload,
  FaceTrainingStatus,
} from "@/types";

export function useEmployees() {
  return useQuery({
    queryKey: ["employees"],
    queryFn: () => apiFetchJson<Employee[]>("/api/v2/employees"),
  });
}

export function useCreateEmployee() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: CreateEmployeePayload) =>
      apiFetchJson<Employee>("/api/v2/employees", {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["employees"] }),
  });
}

export function useDeleteEmployee() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiFetchJson<void>(`/api/v2/employees/${id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["employees"] }),
  });
}

export function useDeleteEmployeeEnrollmentData() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiFetchJson<void>(`/api/v2/employees/${id}/enrollment-data`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["employees"] }),
  });
}

export function useFaceTrainingStatus(employeeId: string | null) {
  return useQuery({
    queryKey: ["face-training-status", employeeId],
    enabled: !!employeeId,
    queryFn: () => apiFetchJson<FaceTrainingStatus>(`/api/v2/employees/${employeeId}/face-training/status`),
    refetchInterval: (query) => {
      const data = query.state.data;
      if (!data) return false;
      return data.state === "capturing" || data.state === "processing" ? 2000 : false;
    },
  });
}

export function useStartFaceTraining() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ employeeId, payload }: { employeeId: string; payload: FaceTrainingStartPayload }) =>
      apiFetchJson<FaceTrainingStatus>(`/api/v2/employees/${employeeId}/face-training/start`, {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    onSuccess: (_data, variables) => {
      qc.invalidateQueries({ queryKey: ["employees"] });
      qc.invalidateQueries({ queryKey: ["face-training-status", variables.employeeId] });
    },
  });
}

export function useCancelFaceTraining() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (employeeId: string) =>
      apiFetchJson<FaceTrainingCancelResponse>(`/api/v2/employees/${employeeId}/face-training/cancel`, {
        method: "POST",
      }),
    onSuccess: (_data, employeeId) => {
      qc.invalidateQueries({ queryKey: ["employees"] });
      qc.invalidateQueries({ queryKey: ["face-training-status", employeeId] });
    },
  });
}
