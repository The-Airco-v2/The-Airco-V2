export type AuthStatus =
  | "loading"
  | "authenticated"
  | "unauthenticated"
  | "not_provisioned"
  | "inactive"
  | "tenant_inactive";

export interface AuthUser {
  id: string;
  email: string;
  name: string;
  role: "admin" | "viewer";
  tenant_id: string;
}

export interface BackendAuthProfile {
  id: string;
  tenant_id: string | null;
  role: "admin" | "viewer" | null;
  is_active: boolean | null;
  email: string | null;
}

export interface BackendAuthResponse {
  accountState:
    | "authenticated"
    | "unauthenticated"
    | "not_provisioned"
    | "inactive_user"
    | "inactive_tenant";
  userId: string | null;
  email: string | null;
  tenantId: string | null;
  role: "admin" | "viewer" | null;
  profile: BackendAuthProfile | null;
  message: string | null;
}

export interface ApiErrorField {
  field: string;
  message: string;
}

export interface ApiErrorResponse {
  detail?:
    | string
    | {
        code?: string;
        message?: string;
        fields?: ApiErrorField[];
      };
  error?: string;
  message?: string;
}

export interface Camera {
  id: string;
  name: string;
  rtsp_url: string;
  location: string | null;
  zone: string | null;
  is_entrance: boolean;
  status: "online" | "offline" | "unknown";
  last_seen: string | null;
  created_at: string;
  stream_name: string;
}

export interface CreateCameraPayload {
  name: string;
  rtsp_url: string;
  location?: string;
  zone?: string;
  is_entrance: boolean;
}

export interface Employee {
  id: string;
  name: string;
  department: string;
  enrollment_status: "trained" | "untrained";
  photo_url: string | null;
  created_at: string;
}

export interface CreateEmployeePayload {
  name: string;
  department: string;
}

export interface Session {
  id: string;
  name: string;
  status: "running" | "stopped" | "paused";
  reid_profile: "standard" | "ultimate";
  camera_ids: string[];
  camera_count: number;
  created_at: string;
  started_at: string | null;
  stopped_at: string | null;
}

export interface CreateSessionPayload {
  name: string;
  camera_ids: string[];
}

export type SessionReIdProfile = "standard" | "ultimate";

export interface StartSessionPayload {
  id: string;
  reid_profile: SessionReIdProfile;
}

export interface UltimateRuntimeWorkerStatus {
  session_id: string;
  camera_id: string;
  rtsp_url: string;
  frames_processed: number;
  last_frame_at: string | null;
  last_error: string | null;
  running: boolean;
}

export interface UltimateRuntimeStatus {
  status: "ok" | "degraded" | "unknown";
  selector: "standard" | "ultimate";
  active_session_id: string | null;
  active_camera_count: number;
  worker_count: number;
  last_heartbeat_at: string | null;
  workers: UltimateRuntimeWorkerStatus[];
}

export interface GpuStatus {
  type: "runpod" | "local";
  is_enabled: boolean;
  status: "ON" | "OFF";
  gpu_name: string | null;
  gpu_count: number | null;
  gpu_id: string | null;
  memory: string | null;
  configuration: Record<string, string> | null;
}

export type AlertSeverity = "critical" | "high" | "medium" | "low";

export interface Alert {
  id: string;
  type: string;
  severity: AlertSeverity;
  camera_id: string | null;
  camera_name: string | null;
  session_id: string;
  message: string;
  acknowledged: boolean;
  created_at: string;
  evidence_url: string | null;
  snapshot_url: string | null;
}

export interface OverviewTodaySession {
  id: string;
  name: string;
  status: "stopped" | "running" | "paused";
  created_at: string;
  started_at: string | null;
  stopped_at: string | null;
}

export interface OverviewToday {
  session: OverviewTodaySession | null;
  summary: {
    counts: {
      expected: number;
      present: number;
      late: number;
      absent: number;
      unknown: number;
      active_exceptions: number;
    };
    phone: { violators: number; total_minutes: number };
    health: {
      camera_total: number;
      camera_active: number;
      entrance_cameras: number;
      coverage_status: "healthy" | "degraded" | "critical";
    };
  };
}

export interface EmployeeIntelligence {
  employee_id: string | null;
  employee_name: string;
  training_status: "trained" | "untrained";
  presence: {
    is_present: boolean;
    entered_at: string | null;
    last_seen: string | null;
  };
  live_status: "working" | "idle" | "walking" | "unknown";
  location: {
    current_zone: string | null;
    current_camera: string | null;
  };
  movement_path: Array<{ zone: string; time: string }>;
  productivity: {
    working_seconds: number;
    idle_seconds: number;
    productivity_percent: number;
  };
  dwell_analysis: Record<string, number>;
  violations: {
    phone_usage_minutes: number;
    phone_violation: boolean;
    restricted_zone_violation: boolean;
  };
  recognition_state: string;
  best_thumbnail_url: string | null;
  confidence: number;
}

export interface UnknownPersonAlertSummary {
  id: string;
  type: string;
  severity: AlertSeverity;
  status: string;
  created_at?: string;
  message?: string;
  evidence_url?: string | null;
}

export interface UnknownPersonSummary {
  person_id: string;
  display_name: string;
  recognition_state: string;
  is_active: boolean;
  first_seen_at: string | null;
  last_seen_at: string | null;
  face_confidence: number;
  body_confidence: number;
  best_thumbnail_url: string | null;
  current_camera: string | null;
  current_zone: string | null;
  dwell_seconds: number;
  active_alert_count: number;
  active_alert_types: string[];
  risk_level: "low" | "medium" | "high";
  risk_reasons: string[];
  continuity_confidence: number;
  continuity_reasons: string[];
  evidence_summary: Record<string, unknown>;
}

export interface UnknownPersonsSummary {
  unknown_persons: number;
  active_unknown_persons: number;
  active_alerts: number;
  high_risk_persons: number;
  average_face_confidence: number;
}

export interface UnknownPersonsResponse {
  session_id: string;
  summary: UnknownPersonsSummary;
  persons: UnknownPersonSummary[];
}

export interface UnknownPersonDetailPerson extends UnknownPersonSummary {
  phone_usage_minutes: number;
}

export interface UnknownPersonTimelineMoment {
  id: string;
  kind: "first_seen" | "dwell_checkpoint" | "camera_transition" | "zone_transition" | "alert_backed" | "latest_seen";
  occurred_at: string | null;
  camera_id: string | null;
  camera_name: string | null;
  zone: string | null;
  image_url: string | null;
  thumbnail_url: string | null;
  selection_reason: string | null;
  to_camera_name?: string | null;
  to_zone?: string | null;
  alert_type?: string;
  segment_id?: string;
}

export interface UnknownPersonStoryboardItem extends UnknownPersonTimelineMoment {
  thumbnail_url: string | null;
}

export interface UnknownPersonTimeline {
  window_start: string | null;
  window_end: string | null;
  moments: UnknownPersonTimelineMoment[];
}

export interface UnknownPersonDwellEntry {
  camera_id: string;
  camera_name: string | null;
  zone: string | null;
  dwell_seconds: number;
}

export interface UnknownPersonViolations {
  active_alert_count: number;
  active_alert_types: string[];
  phone_usage_minutes: number;
  identity_conflict: boolean;
  low_face_confidence: boolean;
}

export interface UnknownPersonRiskContext {
  risk_level: "low" | "medium" | "high";
  risk_score: number;
  risk_factors: string[];
  recommended_action: string;
}

export interface UnknownPersonDetail {
  session_id: string;
  person: UnknownPersonDetailPerson;
  timeline: UnknownPersonTimeline;
  storyboard: UnknownPersonStoryboardItem[];
  dwell_analysis: {
    total_seconds: number;
    by_camera: UnknownPersonDwellEntry[];
  };
  violations: UnknownPersonViolations;
  risk_context: UnknownPersonRiskContext;
}

export interface IdentityClusterSummary {
  id: string;
  employee_id: string | null;
  cluster_state: "anonymous" | "employee_linked" | "superseded";
  display_label: string | null;
}

export interface IdentityReviewSummary {
  id: string;
  type: "unknown_merge" | "assign_employee" | "split_member" | "undo_review";
  decision: "confirmed" | "reverted";
}

export interface IdentityReviewMutationResponse {
  cluster?: IdentityClusterSummary;
  review: IdentityReviewSummary;
  merged_person_ids?: string[];
  person?: {
    id: string;
    employee_id: string | null;
    recognition_state: string | null;
    identity_cluster_id: string | null;
  };
}

export type IdentityReviewScope = "active_session" | "cross_session";
export type IdentityReviewItemKind =
  | "merge_suggestion"
  | "cluster_candidate"
  | "employee_candidate"
  | "manual_review";
export type IdentityReviewItemStatus = "open" | "reviewed" | "reverted";

export interface IdentityReviewQueueItem {
  review_item_id: string;
  scope: IdentityReviewScope;
  kind: IdentityReviewItemKind;
  status: IdentityReviewItemStatus;
  session_id: string | null;
  session_name: string | null;
  source_person_id: string;
  source_cluster_id: string | null;
  representative_thumbnail_url: string | null;
  display_name: string | null;
  confidence: number;
  candidate_count: number;
  reason_tags: string[];
  created_at: string | null;
  last_seen_at: string | null;
}

export interface IdentityReviewQueueResponse {
  scope: IdentityReviewScope;
  items: IdentityReviewQueueItem[];
}

export interface IdentityReviewWorkspaceSource {
  person_id: string;
  display_name: string | null;
  best_thumbnail_url: string | null;
  session_id: string | null;
  session_name: string | null;
  identity_cluster_id: string | null;
  recognition_state: string | null;
  confidence: number;
  reason_tags: string[];
  first_seen_at: string | null;
  last_seen_at: string | null;
}

export interface IdentityReviewWorkspaceCandidate {
  person_id?: string;
  cluster_id?: string;
  display_name: string | null;
  best_thumbnail_url: string | null;
  confidence: number;
  identity_cluster_id?: string | null;
  employee_id?: string | null;
  cluster_state?: string | null;
  reason_tags: string[];
}

export interface IdentityReviewHistoryItem {
  review_id: string;
  review_type: string;
  decision: string;
  source_session_person_id?: string | null;
  target_session_person_id?: string | null;
  target_employee_id?: string | null;
  source_cluster_id?: string | null;
  target_cluster_id?: string | null;
  reason?: string | null;
  created_at: string | null;
  reverted_at?: string | null;
}

export interface IdentityReviewItemDetail {
  review_item_id: string;
  scope: IdentityReviewScope;
  kind: IdentityReviewItemKind;
  status: IdentityReviewItemStatus;
  source: IdentityReviewWorkspaceSource;
  candidates: IdentityReviewWorkspaceCandidate[];
  history: IdentityReviewHistoryItem[];
}

export interface IdentityReviewHistoryResponse {
  items: IdentityReviewHistoryItem[];
}

export interface MergeUnknownPersonsPayload {
  source_person_id: string;
  target_person_ids: string[];
  reason?: string;
}

export interface AssignEmployeeIdentityPayload {
  source_person_id: string;
  employee_id: string;
  reason?: string;
}

export interface UndoIdentityReviewPayload {
  reason?: string;
}

export interface ReportSessionStub {
  id: string;
  name: string | null;
  status: "running" | "stopped" | "paused" | string | null;
}

export interface ReportTodaySummary {
  session: ReportSessionStub | null;
  cards: {
    present_today: number;
    absent_today: number;
    late_arrivals: number;
    avg_productivity: number;
    active_cameras: {
      online: number;
      total: number;
    };
    footfall_today: number;
  };
}

export interface ReportTodayInsightEmployee {
  employee_id: string;
  employee_name: string;
  department: string | null;
}

export interface ReportTodayInsights {
  session: ReportSessionStub | null;
  cards: {
    late_arrivals: Array<
      ReportTodayInsightEmployee & {
        first_entry: string | null;
      }
    >;
    on_time: Array<
      ReportTodayInsightEmployee & {
        first_entry: string | null;
      }
    >;
    phone_usage: Array<
      ReportTodayInsightEmployee & {
        phone_usage_minutes: number;
      }
    >;
    violations: Array<
      ReportTodayInsightEmployee & {
        violations: number;
      }
    >;
  };
}

export interface ReportAttendanceRow {
  employee_id: string;
  employee_name: string;
  department: string | null;
  first_entry: string | null;
  last_seen: string | null;
  total_work_minutes: number;
  status: "on_time" | "late" | "absent" | string;
  violations: number;
  productivity_percent: number;
}

export interface ReportTodayAttendanceLog {
  session: ReportSessionStub | null;
  rows: ReportAttendanceRow[];
}

export interface ReportDaySummary {
  date: string;
  label: string;
  summary: {
    present: number;
    absent: number;
    late_arrivals: number;
    avg_productivity: number;
    active_cameras: number;
    footfall: number;
    violations: number;
  };
  employees: ReportAttendanceRow[];
}

export interface ReportMonthlyTimeline {
  days: Array<{
    date: string;
    label: string;
    summary: ReportDaySummary["summary"];
  }>;
}

export interface ReportEmployeeAnalysisRow {
  employee_id: string;
  employee_name: string;
  department: string | null;
  avg_productivity_percent: number;
  late_count: number;
  avg_work_hours: number;
  days_present: number;
  days_absent: number;
  violations: number;
}

export interface ReportEmployeeAnalysis {
  days: number;
  employees: ReportEmployeeAnalysisRow[];
}

export interface ReportLeaderboards {
  days: number;
  performers: Array<{ rank: number; employee_name: string; score: number }>;
  attendance: Array<{ rank: number; employee_name: string; attendance_percent: number }>;
  late_behavior: Array<{ rank: number; employee_name: string; late_count: number }>;
  low_work_hours: Array<{ rank: number; employee_name: string; avg_work_hours: number }>;
}
