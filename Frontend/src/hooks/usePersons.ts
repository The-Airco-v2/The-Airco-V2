import { useQuery } from "@tanstack/react-query";
import { apiFetchJson } from "@/lib/api";
import type { UnknownPersonDetail, UnknownPersonsResponse } from "@/types";

export const unknownPersonsQueryKey = (sessionId: string | null) =>
  ["unknown-persons", sessionId] as const;

export const unknownPersonDetailQueryKey = (personId: string | null, sessionId: string | null) =>
  ["unknown-person-detail", personId, sessionId] as const;

export function createUnknownPersonDetailPath(personId: string, sessionId: string | null) {
  const params = new URLSearchParams();
  if (sessionId) {
    params.set("session_id", sessionId);
  }
  const query = params.toString();
  return `/api/v2/persons/unknown/${personId}${query ? `?${query}` : ""}`;
}

export function useUnknownPersons(sessionId: string | null) {
  return useQuery({
    queryKey: unknownPersonsQueryKey(sessionId),
    queryFn: () =>
      apiFetchJson<UnknownPersonsResponse>(
        `/api/v2/persons/unknown?session_id=${encodeURIComponent(sessionId ?? "")}`,
      ),
    enabled: !!sessionId,
    refetchInterval: 30_000,
  });
}

export function useUnknownPersonDetail(personId: string | null, sessionId: string | null) {
  return useQuery({
    queryKey: unknownPersonDetailQueryKey(personId, sessionId),
    queryFn: () =>
      apiFetchJson<UnknownPersonDetail>(createUnknownPersonDetailPath(personId ?? "", sessionId)),
    enabled: !!personId && !!sessionId,
  });
}
