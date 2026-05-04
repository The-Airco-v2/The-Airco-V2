import { useQuery } from "@tanstack/react-query";
import { apiFetchJson } from "@/lib/api";
import type {
  IdentityReviewHistoryResponse,
  IdentityReviewItemDetail,
  IdentityReviewQueueResponse,
  IdentityReviewScope,
} from "@/types";

export const identityReviewQueueQueryKey = (
  scope: IdentityReviewScope,
  sessionId: string | null,
) => ["identity-review", "queue", scope, sessionId] as const;

export const identityReviewItemQueryKey = (itemId: string | null) =>
  ["identity-review", "item", itemId] as const;

export const identityReviewHistoryQueryKey = ["identity-review", "history"] as const;

function createQueuePath(scope: IdentityReviewScope, sessionId: string | null) {
  const params = new URLSearchParams();
  params.set("scope", scope);
  if (sessionId) {
    params.set("session_id", sessionId);
  }
  return `/api/v2/identity-reviews/queue?${params.toString()}`;
}

export function useIdentityReviewQueue(scope: IdentityReviewScope, sessionId: string | null) {
  return useQuery({
    queryKey: identityReviewQueueQueryKey(scope, sessionId),
    queryFn: () => apiFetchJson<IdentityReviewQueueResponse>(createQueuePath(scope, sessionId)),
    enabled: scope === "cross_session" || !!sessionId,
    refetchInterval: scope === "active_session" ? 30_000 : 60_000,
  });
}

export function useIdentityReviewItem(itemId: string | null) {
  return useQuery({
    queryKey: identityReviewItemQueryKey(itemId),
    queryFn: () => apiFetchJson<IdentityReviewItemDetail>(`/api/v2/identity-reviews/items/${itemId ?? ""}`),
    enabled: !!itemId,
  });
}

export function useIdentityReviewHistory() {
  return useQuery({
    queryKey: identityReviewHistoryQueryKey,
    queryFn: () => apiFetchJson<IdentityReviewHistoryResponse>("/api/v2/identity-reviews/history"),
    staleTime: 30_000,
  });
}
