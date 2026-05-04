import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { subscribeToTenantOverview } from "@/lib/live/subscribe";
import { applyOverviewLiveEvent } from "@/hooks/useSessions";

export function useLiveOverview(tenantId: string | null) {
  const queryClient = useQueryClient();

  useEffect(() => {
    if (!tenantId) {
      return;
    }

    return subscribeToTenantOverview(tenantId, (event) => {
      applyOverviewLiveEvent(queryClient, event);
    });
  }, [queryClient, tenantId]);
}
