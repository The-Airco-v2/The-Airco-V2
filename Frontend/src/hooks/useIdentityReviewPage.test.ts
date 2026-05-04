import { describe, expect, it, vi } from "vitest";
import {
  identityReviewHistoryQueryKey,
  identityReviewItemQueryKey,
  identityReviewQueueQueryKey,
  useIdentityReviewHistory,
  useIdentityReviewItem,
  useIdentityReviewQueue,
} from "@/hooks/useIdentityReviewPage";

const { apiFetchJsonMock, useQueryMock } = vi.hoisted(() => ({
  apiFetchJsonMock: vi.fn(),
  useQueryMock: vi.fn((options: unknown) => options),
}));

vi.mock("@tanstack/react-query", () => ({
  useQuery: useQueryMock,
}));

vi.mock("@/lib/api", () => ({
  apiFetchJson: apiFetchJsonMock,
}));

describe("identity review page hooks", () => {
  it("builds the active-session queue request with scope and session id", async () => {
    useIdentityReviewQueue("active_session", "session-1");
    const hook = useQueryMock.mock.calls.at(-1)?.[0] as {
      queryKey: readonly unknown[];
      queryFn: () => Promise<unknown>;
      enabled: boolean;
    };

    await hook.queryFn();

    expect(hook.queryKey).toEqual(identityReviewQueueQueryKey("active_session", "session-1"));
    expect(hook.enabled).toBe(true);
    expect(apiFetchJsonMock).toHaveBeenCalledWith(
      "/api/v2/identity-reviews/queue?scope=active_session&session_id=session-1",
    );
  });

  it("builds the item detail request with the item id", async () => {
    useIdentityReviewItem("active:person-1");
    const hook = useQueryMock.mock.calls.at(-1)?.[0] as {
      queryKey: readonly unknown[];
      queryFn: () => Promise<unknown>;
      enabled: boolean;
    };

    await hook.queryFn();

    expect(hook.queryKey).toEqual(identityReviewItemQueryKey("active:person-1"));
    expect(hook.enabled).toBe(true);
    expect(apiFetchJsonMock).toHaveBeenCalledWith("/api/v2/identity-reviews/items/active:person-1");
  });

  it("builds the history request", async () => {
    useIdentityReviewHistory();
    const hook = useQueryMock.mock.calls.at(-1)?.[0] as {
      queryKey: readonly unknown[];
      queryFn: () => Promise<unknown>;
    };

    await hook.queryFn();

    expect(hook.queryKey).toEqual(identityReviewHistoryQueryKey);
    expect(apiFetchJsonMock).toHaveBeenCalledWith("/api/v2/identity-reviews/history");
  });
});
