import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch, apiFetchJson } from "@/lib/api";
import type { CreateEmployeePayload, Employee } from "@/types";

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
    mutationFn: (id: string) => apiFetchJson(`/api/v2/employees/${id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["employees"] }),
  });
}

export function useEnrollEmployee() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, file, angle }: { id: string; file: File; angle: string }) => {
      const form = new FormData();
      form.append("file", file);
      return apiFetch(`/api/v2/employees/${id}/enroll?angle=${angle}`, {
        method: "POST",
        body: form,
        headers: {},
      });
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["employees"] }),
  });
}
