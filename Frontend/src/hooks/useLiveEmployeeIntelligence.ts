import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { subscribeToSession } from "@/lib/live/subscribe";
import { applyEmployeeIntelligenceLiveEvent } from "@/hooks/useSessions";

export function useLiveEmployeeIntelligence(sessionId: string | null) {
  const queryClient = useQueryClient();

  useEffect(() => {
    if (!sessionId) {
      return;
    }

    return subscribeToSession(sessionId, (event) => {
      applyEmployeeIntelligenceLiveEvent(queryClient, sessionId, event);
    });
  }, [queryClient, sessionId]);
}
