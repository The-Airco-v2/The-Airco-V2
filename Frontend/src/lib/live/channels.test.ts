import { describe, expect, it } from "vitest";
import { alertsChannel, sessionChannel, tenantOverviewChannel } from "@/lib/live/channels";

describe("live channel builders", () => {
  it("builds the tenant overview channel", () => {
    expect(tenantOverviewChannel("tenant-7")).toBe("tenant:tenant-7:overview");
  });

  it("builds the session channel", () => {
    expect(sessionChannel("session-9")).toBe("sessions:session-9");
  });

  it("builds the alerts channel", () => {
    expect(alertsChannel("session-9")).toBe("alerts:session-9");
  });
});
