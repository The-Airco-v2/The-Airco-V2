import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { applyAlertsLiveEvent } from "@/hooks/useAlerts";
import { subscribeToAlerts } from "@/lib/live/subscribe";

export function useLiveAlerts(sessionId: string | null) {
  const queryClient = useQueryClient();

  useEffect(() => {
    if (!sessionId) {
      return;
    }

    return subscribeToAlerts(sessionId, (event) => {
      applyAlertsLiveEvent(queryClient, sessionId, event);
    });
  }, [queryClient, sessionId]);
}
