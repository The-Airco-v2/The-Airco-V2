import { describe, expect, it, vi } from "vitest";
import {
  useAssignEmployeeIdentity,
  useMergeUnknownPersons,
  useUndoIdentityReview,
} from "@/hooks/useIdentityReviews";

const {
  apiFetchJsonMock,
  invalidateQueriesMock,
  useMutationMock,
  useQueryClientMock,
} = vi.hoisted(() => ({
  apiFetchJsonMock: vi.fn(),
  invalidateQueriesMock: vi.fn(),
  useMutationMock: vi.fn((options: unknown) => options),
  useQueryClientMock: vi.fn(() => ({ invalidateQueries: invalidateQueriesMock })),
}));

vi.mock("@tanstack/react-query", () => ({
  useMutation: useMutationMock,
  useQueryClient: useQueryClientMock,
}));

vi.mock("@/lib/api", () => ({
  apiFetchJson: apiFetchJsonMock,
}));

describe("identity review hooks", () => {
  it("posts merge requests to the merge endpoint", async () => {
    useMergeUnknownPersons();
    const hook = useMutationMock.mock.calls.at(-1)?.[0] as {
      mutationFn: (payload: { source_person_id: string; target_person_ids: string[]; reason?: string }) => Promise<unknown>;
      onSuccess: () => void;
    };

    await hook.mutationFn({
      source_person_id: "person-1",
      target_person_ids: ["person-2", "person-3"],
      reason: "same person",
    });
    hook.onSuccess();

    expect(apiFetchJsonMock).toHaveBeenCalledWith("/api/v2/identity-reviews/merge", {
      method: "POST",
      body: JSON.stringify({
        source_person_id: "person-1",
        target_person_ids: ["person-2", "person-3"],
        reason: "same person",
      }),
    });
    expect(invalidateQueriesMock).toHaveBeenCalled();
  });

  it("posts employee assignment requests to the assign endpoint", async () => {
    useAssignEmployeeIdentity();
    const hook = useMutationMock.mock.calls.at(-1)?.[0] as {
      mutationFn: (payload: { source_person_id: string; employee_id: string; reason?: string }) => Promise<unknown>;
    };

    await hook.mutationFn({
      source_person_id: "person-1",
      employee_id: "employee-9",
      reason: "known staff member",
    });

    expect(apiFetchJsonMock).toHaveBeenCalledWith("/api/v2/identity-reviews/assign-employee", {
      method: "POST",
      body: JSON.stringify({
        source_person_id: "person-1",
        employee_id: "employee-9",
        reason: "known staff member",
      }),
    });
  });

  it("posts undo requests to the undo endpoint", async () => {
    useUndoIdentityReview();
    const hook = useMutationMock.mock.calls.at(-1)?.[0] as {
      mutationFn: (payload: { reviewId: string; payload: { reason?: string } }) => Promise<unknown>;
    };

    await hook.mutationFn({
      reviewId: "review-1",
      payload: { reason: "mistaken merge" },
    });

    expect(apiFetchJsonMock).toHaveBeenCalledWith("/api/v2/identity-reviews/review-1/undo", {
      method: "POST",
      body: JSON.stringify({ reason: "mistaken merge" }),
    });
  });
});
