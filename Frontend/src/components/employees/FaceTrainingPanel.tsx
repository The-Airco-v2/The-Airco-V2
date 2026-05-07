import { useEffect, useMemo, useRef, useState } from "react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { StatusBadge } from "@/components/shared/StatusBadge";
import { useCameras } from "@/hooks/useCameras";
import {
  useCancelFaceTraining,
  useFaceTrainingStatus,
  useStartFaceTraining,
} from "@/hooks/useEmployees";
import { useFaceTrainingMetrics, useFaceTrainingPreview, useSystemHealth } from "@/hooks/useSystem";
import type { Employee, FaceTrainingStartPayload, FaceTrainingStatus } from "@/types";
import { toast } from "sonner";

const DEFAULT_TARGET_FRAMES = 100;
const DEFAULT_DURATION_SECONDS = 120;
const ANGLES = ["frontal", "left", "right", "up", "down"] as const;
const GO2RTC_BASE = "/webrtc";

function playerUrl(streamName: string): string {
  return `${GO2RTC_BASE}/stream.html?src=${encodeURIComponent(streamName)}`;
}

function statusMessage(status: FaceTrainingStatus | undefined) {
  if (!status) {
    return "No training in progress. Select an employee and click Start Training to begin.";
  }
  switch (status.state) {
    case "idle":
      return "No training in progress. Select an employee and click Start Training to begin.";
    case "capturing":
      return "Capturing high-quality face samples from the selected camera.";
    case "uploading":
      return "Uploading approved samples to MinIO.";
    case "embedding_processing":
    case "processing":
      return "Generating ArcFace embeddings and storing them in PostgreSQL.";
    case "completed":
      return "Training completed successfully. The new embeddings are active for recognition.";
    case "failed":
      return status.error_message ?? "Training failed. Please try again.";
    case "cancelled":
      return "Training was cancelled.";
    default:
      return "Training status unavailable.";
  }
}

function progressWidth(status: FaceTrainingStatus | undefined) {
  return `${Math.min(100, Math.max(0, status?.progress ?? 0))}%`;
}

function stageLabel(status: FaceTrainingStatus | undefined) {
  switch (status?.state) {
    case "capturing":
      return "Capturing";
    case "uploading":
      return "Uploading to MinIO";
    case "embedding_processing":
    case "processing":
      return "Generating Embeddings";
    case "completed":
      return "Completed";
    case "failed":
      return "Failed";
    case "cancelled":
      return "Cancelled";
    default:
      return "Idle";
  }
}

function stageItems(status: FaceTrainingStatus | undefined) {
  const isCapturing = status?.state === "capturing" || status?.state === "uploading" || status?.state === "embedding_processing" || status?.state === "processing" || status?.state === "completed";
  const isUploaded = status?.uploaded_frames ?? 0;
  const isEmbedded = status?.embedded_frames ?? 0;
  return [
    { label: "Camera Connected", done: !!status && status.state !== "idle" },
    { label: "Face Detection Active", done: isCapturing },
    { label: "Quality Validation", done: (status?.captured_frames ?? 0) > 0 || isCapturing },
    { label: "Uploading to MinIO", done: isUploaded > 0 || status?.state === "uploading" || status?.state === "embedding_processing" || status?.state === "completed" },
    { label: "Embedding Generation", done: isEmbedded > 0 || status?.state === "embedding_processing" || status?.state === "completed" },
    { label: "PostgreSQL Storage", done: status?.state === "completed" },
    { label: "Completed", done: status?.state === "completed" },
  ];
}

function pipelineSectionTitle(status: FaceTrainingStatus | undefined) {
  return status?.state === "completed"
    ? "Training complete"
    : status?.state === "failed"
      ? "Training failed"
      : status?.state === "cancelled"
        ? "Training cancelled"
        : "Training pipeline";
}

function countBar(label: string, value: number, total: number, tone: string) {
  const pct = total > 0 ? Math.min(100, Math.round((value / total) * 100)) : 0;
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between text-xs text-zinc-400">
        <span>{label}</span>
        <span>{value} / {total}</span>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-zinc-800">
        <div className={`h-full rounded-full ${tone}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

function PreviewTile({ label, image }: { label: string; image: string | null | undefined }) {
  return (
    <div className="overflow-hidden rounded-xl border border-zinc-800 bg-zinc-950">
      <div className="border-b border-zinc-800 px-3 py-2 text-xs uppercase tracking-wide text-zinc-500">{label}</div>
      <div className="flex aspect-square items-center justify-center bg-black">
        {image ? (
          <img src={`data:image/jpeg;base64,${image}`} alt={label} className="h-full w-full object-cover" />
        ) : (
          <span className="text-xs text-zinc-600">No preview available</span>
        )}
      </div>
    </div>
  );
}

function FaceTrainingStatusCard({
  status,
  preview,
}: {
  status?: FaceTrainingStatus;
  preview?: {
    current_face_image: string | null;
    last_accepted_image: string | null;
    last_rejected_image: string | null;
    rejection_reason: string | null;
  };
}) {
  const pipeline = stageItems(status);
  const target = status?.target_frames ?? DEFAULT_TARGET_FRAMES;
  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-950 p-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-sm font-semibold text-zinc-100">Training Status</p>
          <p className="mt-1 text-sm text-zinc-400">{statusMessage(status)}</p>
          <p className="mt-2 text-xs uppercase tracking-wide text-zinc-500">
            {pipelineSectionTitle(status)} · {stageLabel(status)}
          </p>
        </div>
        <StatusBadge
          status={
            status?.state === "completed"
              ? "trained"
              : status?.state === "failed"
                ? "untrained"
                : status?.state === "cancelled"
                  ? "stopped"
                  : status?.state === "capturing"
                    ? "running"
                    : status?.state === "uploading" || status?.state === "embedding_processing" || status?.state === "processing"
                      ? "working"
                    : "unknown"
          }
        />
      </div>

      <div className="mt-4 h-2 overflow-hidden rounded-full bg-zinc-800">
        <div className="h-full rounded-full bg-sky-500 transition-all" style={{ width: progressWidth(status) }} />
      </div>

      <div className="mt-4 grid grid-cols-1 gap-3 text-xs text-zinc-400 sm:grid-cols-2 xl:grid-cols-4">
        <Metric label="Capture Progress" value={`${status?.captured_frames ?? 0} / ${target}`} />
        <Metric label="Upload Progress" value={`${status?.uploaded_frames ?? 0} / ${status?.accepted_frames ?? 0}`} />
        <Metric label="Embedding Progress" value={`${status?.embedded_frames ?? 0} / ${status?.accepted_frames ?? 0}`} />
        <Metric label="Overall Progress" value={`${status?.progress ?? 0}%`} />
      </div>

      <div className="mt-4 grid gap-3">
        {countBar("Captured", status?.captured_frames ?? 0, target, "bg-sky-500")}
        {countBar("Accepted", status?.accepted_frames ?? 0, target, "bg-emerald-500")}
        {countBar("Rejected", status?.rejected_frames ?? 0, target, "bg-rose-500")}
        {countBar("Uploaded to MinIO", status?.uploaded_frames ?? 0, Math.max(status?.accepted_frames ?? 0, 1), "bg-cyan-500")}
        {countBar("Embeddings Stored", status?.embedded_frames ?? 0, Math.max(status?.accepted_frames ?? 0, 1), "bg-violet-500")}
      </div>

      <div className="mt-4 rounded-lg border border-zinc-800 bg-zinc-900 p-3">
        <p className="text-xs font-semibold uppercase tracking-wide text-zinc-500">Pipeline Stages</p>
        <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {pipeline.map((item) => (
            <div key={item.label} className="rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-xs text-zinc-300">
              <span className={`mr-2 inline-flex h-2 w-2 rounded-full ${item.done ? "bg-emerald-400" : "bg-zinc-600"}`} />
              {item.label}
            </div>
          ))}
        </div>
      </div>

      <div className="mt-4 grid grid-cols-1 gap-3 text-xs text-zinc-400 sm:grid-cols-3">
        <Metric label="Detector faces" value={`${status?.detector_face_count ?? 0}`} />
        <Metric label="Confidence" value={status?.detector_confidence != null ? status.detector_confidence.toFixed(3) : "—"} />
        <Metric label="Debug" value={status?.debug_mode ? "On" : "Off"} />
      </div>

      <p className="mt-2 text-xs leading-5 text-zinc-500">
        Current detector note: <span className="text-zinc-300">{status?.rejection_reason ?? "No rejection on latest frame"}</span>
      </p>

      <p className="mt-2 text-xs leading-5 text-zinc-500">
        Uploads are stored in MinIO and embeddings are persisted in PostgreSQL after the capture phase completes.
      </p>

      <div className="mt-4 grid gap-3 md:grid-cols-3">
        <PreviewTile label="Current Face" image={preview?.current_face_image} />
        <PreviewTile label="Last Accepted" image={preview?.last_accepted_image} />
        <PreviewTile
          label={preview?.rejection_reason ? `Last Rejected (${preview.rejection_reason})` : "Last Rejected"}
          image={preview?.last_rejected_image}
        />
      </div>

      {status && status.state === "capturing" && status.accepted_frames === 0 && status.rejected_frames > 0 && (
        <Alert variant="default" className="mt-4 border-amber-500/30 bg-amber-500/10 text-amber-100">
          <AlertDescription className="text-sm text-amber-100">
            No approved face samples have been captured yet. Rejections usually mean the face is not centered,
            too far away, poorly lit, blurred, or not visible in the camera. You can reposition the employee or
            press Stop Training to end this session.
          </AlertDescription>
        </Alert>
      )}

      <div className="mt-4 flex flex-wrap gap-2">
        {ANGLES.map((angle) => (
          <div
            key={angle}
            className="min-w-24 rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-2 text-xs capitalize text-zinc-300"
          >
            <div className="flex items-center justify-between gap-3">
              <span>{angle}</span>
              <span className="text-zinc-100">{status?.angle_coverage?.[angle] ?? 0}</span>
            </div>
            <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-zinc-800">
              <div
                className="h-full rounded-full bg-sky-400"
                style={{ width: `${Math.min(100, ((status?.angle_coverage?.[angle] ?? 0) / Math.max(status?.accepted_frames ?? 1, 1)) * 100)}%` }}
              />
            </div>
          </div>
        ))}
      </div>

      {status?.export_object_name && (
        <p className="mt-4 break-all text-xs text-zinc-500">
          Export artifact: <span className="text-zinc-300">{status.export_object_name}</span>
        </p>
      )}
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-2">
      <p className="text-[11px] uppercase tracking-wide text-zinc-500">{label}</p>
      <p className="mt-1 text-sm font-medium text-zinc-100">{value}</p>
    </div>
  );
}

export function FaceTrainingPanel({ employees }: { employees: Employee[] | undefined }) {
  const { data: cameras } = useCameras();
  const { data: systemHealth } = useSystemHealth();
  const { data: metrics } = useFaceTrainingMetrics();
  const [employeeId, setEmployeeId] = useState<string>("");
  const [cameraId, setCameraId] = useState<string>("");
  const [debugMode, setDebugMode] = useState(false);
  const lastNotifiedStateRef = useRef<{ jobId: string | null; state: FaceTrainingStatus["state"] | null }>({
    jobId: null,
    state: null,
  });

  const selectedEmployee = useMemo(
    () => employees?.find((employee) => employee.id === employeeId) ?? null,
    [employees, employeeId],
  );
  const selectedCamera = useMemo(
    () => cameras?.find((camera) => camera.id === cameraId) ?? null,
    [cameras, cameraId],
  );

  const statusQuery = useFaceTrainingStatus(employeeId || null);
  const status = statusQuery.data;
  const previewQuery = useFaceTrainingPreview(status?.job_id ?? null);
  const preview = previewQuery.data;
  const startTraining = useStartFaceTraining();
  const cancelTraining = useCancelFaceTraining();
  const isActive =
    status?.state === "capturing" ||
    status?.state === "uploading" ||
    status?.state === "embedding_processing" ||
    status?.state === "processing";

  useEffect(() => {
    if (!status?.job_id || !status.state) {
      return;
    }

    const previous = lastNotifiedStateRef.current;
    if (previous.jobId === status.job_id && previous.state === status.state) {
      return;
    }

    lastNotifiedStateRef.current = { jobId: status.job_id, state: status.state };

    if (status.state === "completed") {
      toast.success(`Face training completed for ${status.employee_name}`);
      return;
    }
    if (status.state === "failed") {
      toast.error(status.error_message ?? `Face training failed for ${status.employee_name}`);
      return;
    }
    if (status.state === "cancelled") {
      toast.info(`Face training cancelled for ${status.employee_name}`);
    }
  }, [status]);

  useEffect(() => {
    const firstEmployeeId = employees?.[0]?.id ?? "";
    if (!employeeId && firstEmployeeId) {
      setEmployeeId(firstEmployeeId);
    }
    if (employeeId && !(employees ?? []).some((employee) => employee.id === employeeId)) {
      setEmployeeId(firstEmployeeId);
    }
  }, [employees, employeeId]);

  useEffect(() => {
    const firstCameraId = cameras?.[0]?.id ?? "";
    const lockCameraToJob =
      status?.state === "capturing" ||
      status?.state === "uploading" ||
      status?.state === "embedding_processing" ||
      status?.state === "processing";
    if (lockCameraToJob && status?.camera_id) {
      if (cameraId !== status.camera_id) {
        setCameraId(status.camera_id);
      }
      return;
    }
    if (!cameraId && firstCameraId) {
      setCameraId(firstCameraId);
    }
    if (cameraId && !(cameras ?? []).some((camera) => camera.id === cameraId)) {
      setCameraId(firstCameraId);
    }
  }, [cameras, cameraId, status?.camera_id, status?.state]);

  const start = (replaceExistingValue: boolean) => {
    if (!employeeId) {
      toast.error("Select an employee first");
      return;
    }
    if (!cameraId) {
      toast.error("Select a camera first");
      return;
    }
    if (!selectedEmployee) {
      toast.error("Selected employee is not available yet");
      return;
    }
    if (!selectedCamera) {
      toast.error("Selected camera is not available yet");
      return;
    }

    const payload: FaceTrainingStartPayload = {
      camera_id: cameraId,
      camera_name: selectedCamera?.name ?? "",
      employee_name: selectedEmployee?.name ?? "",
      replace_existing: replaceExistingValue,
      target_frames: DEFAULT_TARGET_FRAMES,
      duration_seconds: DEFAULT_DURATION_SECONDS,
      debug_mode: debugMode,
    };

    startTraining.mutate(
      { employeeId, payload },
      {
        onSuccess: () => {
          toast.success(replaceExistingValue ? "Retraining started" : "Training started");
        },
        onError: (error: unknown) => {
          toast.error(error instanceof Error ? error.message : "Unable to start training");
        },
      },
    );
  };

  const cancel = () => {
    if (!employeeId) return;
    cancelTraining.mutate(employeeId, {
      onSuccess: (data) => {
        if (data.state === "idle") {
          toast.info(data.message);
          return;
        }
        toast.info(data.message);
      },
      onError: (error: unknown) => toast.error(error instanceof Error ? error.message : "Unable to cancel training"),
    });
  };

  return (
    <section className="space-y-4 rounded-xl border border-zinc-800 bg-zinc-900 p-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-base font-semibold text-zinc-50">Face Training Session</h2>
          <p className="mt-1 text-sm text-zinc-400">
            Guided capture for employees using the selected camera. The system filters poor frames and stores the
            best ArcFace embeddings in the database.
          </p>
        </div>
        <div className="text-right text-xs text-zinc-500">
          <p>2 min / {DEFAULT_TARGET_FRAMES} samples target</p>
          <p>Approved frames are saved to MinIO, embeddings to PostgreSQL</p>
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-[1.1fr_0.9fr]">
        <div className="space-y-4 rounded-xl border border-zinc-800 bg-zinc-950 p-4">
          <div className="grid gap-3 sm:grid-cols-4">
            <Metric label="SCRFD" value={systemHealth?.triton.status === "online" ? "Online" : systemHealth?.triton.status ?? "—"} />
            <Metric label="MinIO" value={systemHealth?.minio.status === "online" ? "Online" : systemHealth?.minio.status ?? "—"} />
            <Metric label="PostgreSQL" value={systemHealth?.postgresql.status === "online" ? "Online" : systemHealth?.postgresql.status ?? "—"} />
            <Metric label="Workers Active" value={`${metrics?.workers_active ?? 0}`} />
          </div>

          <div className="rounded-xl border border-zinc-800 bg-zinc-900 px-3 py-2 text-xs text-zinc-400">
            <span className="font-medium text-zinc-200">Triton models:</span>{" "}
            {systemHealth?.triton.models_ready?.length ? systemHealth.triton.models_ready.join(", ") : "none ready"}
            {systemHealth?.triton.models_unready?.length ? (
              <span className="text-amber-300"> · missing: {systemHealth.triton.models_unready.join(", ")}</span>
            ) : null}
          </div>

          <div className="grid gap-3 sm:grid-cols-3">
            <Metric label="Queue Depth" value={`${metrics?.queue_depth ?? 0}`} />
            <Metric label="Avg Embed Time" value={`${metrics?.average_embedding_ms?.toFixed(1) ?? "0.0"} ms`} />
            <Metric label="Images Uploaded" value={`${metrics?.images_uploaded ?? 0}`} />
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-2">
              <Label className="text-zinc-300">Select Employee</Label>
              <Select value={employeeId} onValueChange={setEmployeeId}>
                <SelectTrigger className="border-zinc-700 bg-zinc-900 text-zinc-50">
                  <SelectValue placeholder="-- Choose Employee --" />
                </SelectTrigger>
                <SelectContent className="border-zinc-700 bg-zinc-900">
                  {(employees ?? []).map((employee) => (
                    <SelectItem key={employee.id} value={employee.id} className="text-zinc-300">
                      {employee.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-2">
              <Label className="text-zinc-300">Select Camera</Label>
              <Select value={cameraId} onValueChange={setCameraId} disabled={isActive}>
                <SelectTrigger className="border-zinc-700 bg-zinc-900 text-zinc-50">
                  <SelectValue placeholder="-- Choose Camera --" />
                </SelectTrigger>
                <SelectContent className="border-zinc-700 bg-zinc-900">
                  {(cameras ?? []).map((camera) => (
                    <SelectItem key={camera.id} value={camera.id} className="text-zinc-300">
                      {camera.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="rounded-xl border border-zinc-800 bg-zinc-900 p-4">
            <div className="flex items-start gap-3">
              <Checkbox
                id="face-training-debug-mode"
                checked={debugMode}
                onCheckedChange={(checked) => setDebugMode(checked === true)}
                className="mt-0.5 border-zinc-600 data-[state=checked]:bg-sky-500 data-[state=checked]:text-white"
              />
              <div className="space-y-1">
                <Label htmlFor="face-training-debug-mode" className="text-zinc-200">
                  Debug mode
                </Label>
                <p className="text-xs leading-5 text-zinc-500">
                  Logs detector confidence, face count, and rejection reasons for each processed frame.
                </p>
              </div>
            </div>
          </div>

          <div className="rounded-xl border border-zinc-800 bg-zinc-900 p-4">
            <p className="text-xs uppercase tracking-wide text-zinc-500">Selected Target</p>
            <div className="mt-2 flex flex-wrap gap-2 text-sm text-zinc-200">
              <span className="rounded-full bg-zinc-800 px-3 py-1">{selectedEmployee?.name ?? "No employee selected"}</span>
              <span className="rounded-full bg-zinc-800 px-3 py-1">{selectedCamera?.name ?? "No camera selected"}</span>
            </div>
          </div>

          <div className="overflow-hidden rounded-xl border border-zinc-800 bg-zinc-900">
            <div className="flex items-center justify-between border-b border-zinc-800 px-4 py-3">
              <div>
                <p className="text-sm font-semibold text-zinc-100">Live Camera Preview</p>
                <p className="text-xs text-zinc-500">
                  {selectedCamera?.name ?? "Choose a camera to preview the live feed."}
                </p>
              </div>
              <span className="rounded-full border border-zinc-700 bg-zinc-950 px-2.5 py-1 text-xs text-zinc-400">
                go2rtc
              </span>
            </div>

            <div className="relative aspect-video bg-black">
              {selectedCamera ? (
                <iframe
                  src={playerUrl(selectedCamera.stream_name)}
                  className="h-full w-full border-0"
                  allow="autoplay"
                  title={`Live preview: ${selectedCamera.name}`}
                />
              ) : (
                <div className="flex h-full items-center justify-center text-sm text-zinc-500">
                  Select a camera to load the live preview.
                </div>
              )}

              <div className="pointer-events-none absolute inset-0 bg-gradient-to-t from-black/70 via-black/15 to-transparent" />

              <div className="pointer-events-none absolute inset-0 rounded-xl border-2 border-sky-400/35" />

              <div className="pointer-events-none absolute inset-0 flex flex-col justify-between p-4 text-white">
                <div className="flex items-start justify-end gap-3">
                  <div className="space-y-2 text-right">
                    <div className="rounded-full border border-white/10 bg-black/40 px-3 py-1.5 text-xs backdrop-blur-sm">
                      {status?.state === "capturing" || status?.state === "uploading" || status?.state === "embedding_processing" || status?.state === "processing"
                        ? "Training Active"
                        : "Preview Mode"}
                    </div>
                    <div className="rounded-xl border border-white/10 bg-black/40 px-3 py-2 text-left text-[11px] backdrop-blur-sm">
                      <p className="uppercase tracking-[0.2em] text-zinc-300">Live detector</p>
                      <p className="mt-1 text-zinc-100">
                        Faces: {status?.detector_face_count ?? 0} · Conf: {status?.detector_confidence != null ? status.detector_confidence.toFixed(3) : "—"}
                      </p>
                      <p className="mt-0.5 text-zinc-300">
                        {status?.rejection_reason ? `Rejecting: ${status.rejection_reason}` : "Waiting for a usable frame"}
                      </p>
                    </div>
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
                  {ANGLES.map((angle) => {
                    const coverage = status?.angle_coverage?.[angle] ?? 0;
                    return (
                      <div
                        key={angle}
                        className={[
                          "rounded-lg border px-3 py-2 text-xs backdrop-blur-sm transition-all",
                          "border-white/10 bg-black/35 text-zinc-200",
                        ].join(" ")}
                      >
                        <p className="font-medium capitalize">{angle}</p>
                        <p className="mt-0.5 text-[11px] text-inherit/80">Captured: {coverage}</p>
                      </div>
                    );
                  })}
                </div>
              </div>
            </div>
          </div>

          <div className="flex flex-wrap gap-2">
            <Button
              onClick={() => start(false)}
              disabled={!employeeId || !cameraId || startTraining.isPending || isActive}
              className="bg-sky-500 text-white hover:bg-sky-400"
            >
              {startTraining.isPending && startTraining.variables?.payload.replace_existing === false ? "Starting…" : "Start Training"}
            </Button>
            <Button
              variant="outline"
              onClick={() => start(true)}
              disabled={!employeeId || !cameraId || startTraining.isPending || isActive}
              className="border-zinc-700 bg-zinc-900 text-zinc-200 hover:bg-zinc-800"
            >
              {startTraining.isPending && startTraining.variables?.payload.replace_existing === true ? "Retraining…" : "Re-train Face"}
            </Button>
            <Button
              variant="ghost"
              onClick={cancel}
              disabled={!isActive || cancelTraining.isPending}
              className="border border-red-500/30 bg-red-500/10 text-red-200 hover:bg-red-500/20 hover:text-red-100"
            >
              {cancelTraining.isPending ? "Stopping…" : "Stop Training"}
            </Button>
          </div>

          <Alert variant="default" className="border-zinc-800 bg-zinc-950 text-zinc-200">
            <AlertDescription className="text-sm text-zinc-300">{statusMessage(status)}</AlertDescription>
          </Alert>
        </div>

        <FaceTrainingStatusCard status={status} preview={preview} />
      </div>
    </section>
  );
}
