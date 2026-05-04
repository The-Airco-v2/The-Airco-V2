import { describe, expect, it, vi } from "vitest";
import { createUnknownPersonDetailPath, unknownPersonDetailQueryKey, useUnknownPersonDetail } from "@/hooks/usePersons";
import type { UnknownPersonDetail, UnknownPersonTimelineMoment } from "@/types";

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

describe("unknown person detail contract", () => {
  function makeUnknownPersonDetail(): UnknownPersonDetail {
    const momentKinds = [
      "first_seen",
      "dwell_checkpoint",
      "camera_transition",
      "zone_transition",
      "alert_backed",
      "latest_seen",
    ] satisfies UnknownPersonTimelineMoment["kind"][];

    const moments: UnknownPersonTimelineMoment[] = momentKinds.map((kind, index) => ({
      id: `moment-${index + 1}`,
      kind,
      occurred_at: [
        "2026-04-04T10:00:00Z",
        "2026-04-04T10:03:00Z",
        "2026-04-04T10:06:00Z",
        "2026-04-04T10:09:00Z",
        "2026-04-04T10:12:00Z",
        "2026-04-04T10:15:00Z",
      ][index],
      camera_id: kind === "latest_seen" ? null : "cam-1",
      camera_name: "Lobby",
      zone: "lobby",
      image_url: index === 4 ? "https://example.com/alert.jpg" : null,
      thumbnail_url: index === 5 ? "https://example.com/latest.jpg" : null,
      selection_reason: [
        "first sighting",
        "dwell checkpoint",
        "camera transition",
        "zone transition",
        "alert evidence",
        "latest sighting",
      ][index],
      to_camera_name: kind === "camera_transition" ? "South Hall Camera" : null,
      to_zone: kind === "zone_transition" ? "South Hall" : null,
      alert_type: kind === "alert_backed" ? "unknown_person" : undefined,
      segment_id: kind === "dwell_checkpoint" ? "segment-1" : undefined,
    }));

    return {
      session_id: "session-1",
      person: {
        person_id: "person-1",
        display_name: "Unknown Visitor 01",
        recognition_state: "unknown",
        is_active: true,
        first_seen_at: "2026-04-04T10:00:00Z",
        last_seen_at: "2026-04-04T10:15:00Z",
        face_confidence: 0.81,
        body_confidence: 0.73,
        continuity_confidence: 0.62,
        continuity_reasons: ["shared clothing", "same camera corridor"],
        best_thumbnail_url: null,
        current_camera: "cam-1",
        current_zone: "lobby",
        dwell_seconds: 180,
        active_alert_count: 1,
        active_alert_types: ["loitering"],
        risk_level: "medium",
        risk_reasons: ["active alert"],
        evidence_summary: {},
        phone_usage_minutes: 0,
      },
      timeline: {
        window_start: "2026-04-04T10:00:00Z",
        window_end: "2026-04-04T10:15:00Z",
        moments,
      },
      storyboard: [
        {
          id: "moment-1",
          kind: "first_seen",
          occurred_at: "2026-04-04T10:00:00Z",
          camera_id: "cam-1",
          camera_name: "Lobby",
          zone: "lobby",
          image_url: null,
          thumbnail_url: "https://example.com/thumb-first.jpg",
          selection_reason: "first sighting",
        },
        {
          id: "moment-4",
          kind: "zone_transition",
          occurred_at: "2026-04-04T10:09:00Z",
          camera_id: "cam-1",
          camera_name: "Lobby",
          zone: "lobby",
          image_url: null,
          thumbnail_url: "https://example.com/thumb-zone.jpg",
          selection_reason: "zone transition",
          to_zone: "South Hall",
        },
        {
          id: "moment-6",
          kind: "latest_seen",
          occurred_at: "2026-04-04T10:15:00Z",
          camera_id: null,
          camera_name: "Lobby",
          zone: "lobby",
          image_url: null,
          thumbnail_url: "https://example.com/thumb-latest.jpg",
          selection_reason: "latest sighting",
        },
      ],
      dwell_analysis: {
        total_seconds: 180,
        by_camera: [],
      },
      violations: {
        active_alert_count: 1,
        active_alert_types: ["loitering"],
        phone_usage_minutes: 0,
        identity_conflict: false,
        low_face_confidence: false,
      },
      risk_context: {
        risk_level: "medium",
        risk_score: 0.62,
        risk_factors: ["active alert"],
        recommended_action: "monitor",
      },
    };
  }

  it("includes the active session id in the detail query key", () => {
    expect(unknownPersonDetailQueryKey("person-1", "session-1")).toEqual([
      "unknown-person-detail",
      "person-1",
      "session-1",
    ]);
  });

  it("builds a session-scoped detail URL", () => {
    expect(createUnknownPersonDetailPath("person-1", "session-1")).toBe(
      "/api/v2/persons/unknown/person-1?session_id=session-1",
    );
  });

  it("types the extended unknown-person detail timeline contract", () => {
    const detail = makeUnknownPersonDetail() satisfies UnknownPersonDetail;

    expect(detail.timeline.moments.map((moment) => moment.kind)).toEqual([
      "first_seen",
      "dwell_checkpoint",
      "camera_transition",
      "zone_transition",
      "alert_backed",
      "latest_seen",
    ]);
    expect(detail.timeline.moments[4].alert_type).toBe("unknown_person");
    expect(detail.timeline.moments[2].to_camera_name).toBe("South Hall Camera");
    expect(detail.timeline.moments[3].to_zone).toBe("South Hall");
    expect(detail.timeline.moments[1].segment_id).toBe("segment-1");
    expect(detail.storyboard.map((item) => item.id)).toEqual(["moment-1", "moment-4", "moment-6"]);
    expect(detail.storyboard[2].thumbnail_url).toBe("https://example.com/thumb-latest.jpg");
  });

  it("uses the session-scoped detail path when fetching the extended detail", async () => {
    const detail = makeUnknownPersonDetail() satisfies UnknownPersonDetail;

    apiFetchJsonMock.mockResolvedValueOnce(detail);

    useUnknownPersonDetail("person-1", "session-1");
    const queryOptions = useQueryMock.mock.calls.at(-1)?.[0] as {
      queryFn: () => Promise<UnknownPersonDetail>;
    };
    await expect(queryOptions.queryFn()).resolves.toBe(detail);

    expect(apiFetchJsonMock).toHaveBeenCalledWith(
      "/api/v2/persons/unknown/person-1?session_id=session-1",
    );
    expect(useQueryMock).toHaveBeenCalledWith(
      expect.objectContaining({
        queryKey: ["unknown-person-detail", "person-1", "session-1"],
        enabled: true,
      }),
    );
  });
});
