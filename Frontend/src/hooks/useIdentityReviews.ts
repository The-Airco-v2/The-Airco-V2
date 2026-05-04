import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetchJson } from "@/lib/api";
import type {
  AssignEmployeeIdentityPayload,
  IdentityReviewMutationResponse,
  MergeUnknownPersonsPayload,
  UndoIdentityReviewPayload,
} from "@/types";

function invalidateIdentityQueries(qc: ReturnType<typeof useQueryClient>) {
  qc.invalidateQueries({ queryKey: ["unknown-persons"] });
  qc.invalidateQueries({ queryKey: ["unknown-person-detail"] });
  qc.invalidateQueries({ queryKey: ["overview-today"] });
  qc.invalidateQueries({ queryKey: ["employee-intelligence"] });
}

export function useMergeUnknownPersons() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: MergeUnknownPersonsPayload) =>
      apiFetchJson<IdentityReviewMutationResponse>("/api/v2/identity-reviews/merge", {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    onSuccess: () => invalidateIdentityQueries(qc),
  });
}

export function useAssignEmployeeIdentity() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: AssignEmployeeIdentityPayload) =>
      apiFetchJson<IdentityReviewMutationResponse>("/api/v2/identity-reviews/assign-employee", {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    onSuccess: () => invalidateIdentityQueries(qc),
  });
}

export function useUndoIdentityReview() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ reviewId, payload }: { reviewId: string; payload: UndoIdentityReviewPayload }) =>
      apiFetchJson<IdentityReviewMutationResponse>(`/api/v2/identity-reviews/${reviewId}/undo`, {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    onSuccess: () => invalidateIdentityQueries(qc),
  });
}
