import { Activity, Cpu, Play, PlayCircle, Plus, Square } from "lucide-react";
import { useState } from "react";
import { PageHeader } from "@/components/shared/PageHeader";
import { StatusBadge } from "@/components/shared/StatusBadge";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Sheet, SheetContent, SheetFooter, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { useCameras } from "@/hooks/useCameras";
import {
  useCreateSession,
  useGpuStatus,
  useSessions,
  useStartSession,
  useStopSession,
  useUltimateRuntimeStatus,
} from "@/hooks/useSessions";
import type { SessionReIdProfile } from "@/types";
import { toast } from "sonner";

export default function SessionsPage() {
  const { data: sessions, isLoading, error } = useSessions();
  const { data: ultimateStatus } = useUltimateRuntimeStatus();
  const { data: gpuStatus } = useGpuStatus();
  const { data: cameras } = useCameras();
  const createSession = useCreateSession();
  const startSession = useStartSession();
  const stopSession = useStopSession();

  const [sheetOpen, setSheetOpen] = useState(false);
  const [name, setName] = useState("");
  const [selectedCameraIds, setSelectedCameraIds] = useState<string[]>([]);

  const toggleCamera = (id: string) =>
    setSelectedCameraIds((prev) => (prev.includes(id) ? prev.filter((c) => c !== id) : [...prev, id]));

  const handleCreate = () => {
    createSession.mutate(
      { name, camera_ids: selectedCameraIds },
      {
        onSuccess: () => {
          toast.success("Session created");
          setSheetOpen(false);
          setName("");
          setSelectedCameraIds([]);
        },
        onError: () => toast.error("Failed to create session"),
      },
    );
  };

  const handleStart = (id: string, reidProfile: SessionReIdProfile) => {
    startSession.mutate(
      { id, reid_profile: reidProfile },
      {
        onSuccess: () =>
          toast.success(
            reidProfile === "ultimate"
              ? "Session started with Ultimate RE-ID"
              : "Session started",
          ),
        onError: () => toast.error("Failed to start session"),
      },
    );
  };

  const handleStop = (id: string) => {
    stopSession.mutate(id, {
      onSuccess: () => toast.success("Session stopped"),
      onError: () => toast.error("Failed to stop session"),
    });
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title="Sessions"
        description="Manage camera monitoring sessions"
        actions={
          <Button
            onClick={() => setSheetOpen(true)}
            className="gap-1.5 bg-sky-500 text-white hover:bg-sky-400"
            size="sm"
          >
            <Plus className="h-4 w-4" /> New Session
          </Button>
        }
      />

      <div className="grid gap-6 md:grid-cols-2">
        {/* Ultimate Adapter Status */}
        <div className="rounded-xl border border-zinc-800 bg-zinc-900 p-4">
          <div className="flex items-start justify-between gap-4">
            <div>
              <p className="text-xs uppercase tracking-wide text-zinc-500">Ultimate Adapter</p>
              <p className="mt-1 text-sm font-medium text-zinc-100">
                {ultimateStatus?.status === "ok"
                  ? "Healthy"
                  : ultimateStatus?.status === "degraded"
                    ? "Degraded"
                    : "Unknown"}
              </p>
            </div>
            <Activity className="mt-0.5 h-4 w-4 text-sky-400" />
          </div>
          <div className="mt-3 flex flex-wrap gap-4 text-xs text-zinc-400">
            <span>Active session: {ultimateStatus?.active_session_id ? "Attached" : "Idle"}</span>
            <span>Workers: {ultimateStatus?.worker_count ?? 0}</span>
            <span>Cameras: {ultimateStatus?.active_camera_count ?? 0}</span>
          </div>
        </div>

        {/* Allocated GPU Status */}
        <div className="rounded-xl border border-zinc-800 bg-zinc-900 p-4">
          <div className="flex items-start justify-between gap-4">
            <div>
              <p className="text-xs uppercase tracking-wide text-zinc-500">
                Allocated GPU ({gpuStatus?.type === "runpod" ? "RunPod" : "Local Dev"})
              </p>
              <div className="mt-1 flex items-center gap-2">
                <p className="text-sm font-medium text-zinc-100">
                  {gpuStatus?.gpu_name || "No GPU detected"}
                </p>
                <span className="flex h-2 w-2 relative">
                  <span className={`animate-ping absolute inline-flex h-full w-full rounded-full opacity-75 ${gpuStatus?.status === "ON" ? "bg-emerald-400" : "bg-zinc-500"}`}></span>
                  <span className={`relative inline-flex rounded-full h-2 w-2 ${gpuStatus?.status === "ON" ? "bg-emerald-500" : "bg-zinc-600"}`}></span>
                </span>
                <span className="text-xs text-zinc-400">
                  {gpuStatus?.status === "ON" ? "ON" : "OFF"}
                </span>
              </div>
            </div>
            <Cpu className="mt-0.5 h-4 w-4 text-emerald-400" />
          </div>
          <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-zinc-400">
            <span>Memory: {gpuStatus?.memory || "N/A"}</span>
            <span>ID: <code className="text-zinc-300 font-mono text-[10px]">{gpuStatus?.gpu_id ? (gpuStatus.gpu_id.length > 15 ? `${gpuStatus.gpu_id.substring(0, 12)}...` : gpuStatus.gpu_id) : "N/A"}</code></span>
            {gpuStatus?.configuration?.["VCPU Count"] && (
              <span>VCPUs: {gpuStatus.configuration["VCPU Count"]}</span>
            )}
            {gpuStatus?.configuration?.["Volume Disk"] && (
              <span>Volume: {gpuStatus.configuration["Volume Disk"]}</span>
            )}
          </div>
        </div>
      </div>

      {error && (
        <Alert variant="destructive" className="border-red-800 bg-red-950/40">
          <AlertDescription className="text-red-300">Failed to load sessions.</AlertDescription>
        </Alert>
      )}

      <div className="overflow-hidden rounded-xl border border-zinc-800 bg-zinc-900">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-zinc-800">
              {["Name", "Status", "Engine", "Cameras", "Created", ""].map((h) => (
                <th key={h} className="px-5 py-3 text-left text-xs font-medium uppercase tracking-wide text-zinc-500">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              Array.from({ length: 4 }).map((_, i) => (
                <tr key={i} className="border-b border-zinc-800/50">
                  {Array.from({ length: 6 }).map((__, j) => (
                    <td key={j} className="px-5 py-4">
                      <Skeleton className="h-4 w-full bg-zinc-800" />
                    </td>
                  ))}
                </tr>
              ))
            ) : !sessions?.length ? (
              <tr>
                <td colSpan={6} className="py-16 text-center">
                  <PlayCircle className="mx-auto mb-3 h-8 w-8 text-zinc-700" />
                  <p className="text-sm text-zinc-500">No sessions yet</p>
                </td>
              </tr>
            ) : (
              sessions.map((session) => (
                <tr key={session.id} className="border-b border-zinc-800/50 transition-colors hover:bg-zinc-800/20">
                  <td className="px-5 py-3.5 font-medium text-zinc-100">{session.name}</td>
                  <td className="px-5 py-3.5">
                    <StatusBadge status={session.status} />
                  </td>
                  <td className="px-5 py-3.5">
                    <span
                      className={
                        session.reid_profile === "ultimate"
                          ? "rounded-full border border-sky-500/30 bg-sky-500/10 px-2 py-1 text-xs font-medium text-sky-300"
                          : "rounded-full border border-zinc-700 bg-zinc-800 px-2 py-1 text-xs font-medium text-zinc-300"
                      }
                    >
                      {session.reid_profile === "ultimate" ? "Ultimate" : "Standard"}
                    </span>
                  </td>
                  <td className="px-5 py-3.5 text-zinc-400">{session.camera_count}</td>
                  <td className="px-5 py-3.5 text-xs text-zinc-500">
                    {new Date(session.created_at).toLocaleDateString()}
                  </td>
                  <td className="px-5 py-3.5">
                    <div className="flex items-center justify-end gap-2">
                      {session.status === "stopped" ? (
                        <>
                          <Button
                            size="sm"
                            variant="ghost"
                            className="h-7 gap-1.5 text-xs text-emerald-400 hover:bg-emerald-500/10"
                            onClick={() => handleStart(session.id, "standard")}
                            disabled={startSession.isPending}
                          >
                            <Play className="h-3 w-3" /> Start
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            className="h-7 gap-1.5 text-xs text-sky-400 hover:bg-sky-500/10"
                            onClick={() => handleStart(session.id, "ultimate")}
                            disabled={startSession.isPending}
                          >
                            <Play className="h-3 w-3" /> Ultimate RE-ID
                          </Button>
                        </>
                      ) : (
                        <Button
                          size="sm"
                          variant="ghost"
                          className="h-7 gap-1.5 text-xs text-red-400 hover:bg-red-500/10"
                          onClick={() => handleStop(session.id)}
                          disabled={stopSession.isPending}
                        >
                          <Square className="h-3 w-3" /> Stop
                        </Button>
                      )}
                    </div>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      <Sheet open={sheetOpen} onOpenChange={setSheetOpen}>
        <SheetContent className="w-[420px] border-zinc-800 bg-zinc-900 text-zinc-50">
          <SheetHeader>
            <SheetTitle className="text-zinc-50">New Session</SheetTitle>
          </SheetHeader>
          <div className="mt-6 space-y-5">
            <div className="space-y-1.5">
              <Label className="text-zinc-300">Session Name</Label>
              <Input
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="border-zinc-700 bg-zinc-800 text-zinc-50 focus-visible:ring-sky-500"
              />
            </div>
            <div className="space-y-2">
              <Label className="text-zinc-300">Cameras</Label>
              {!cameras?.length ? (
                <p className="text-sm text-zinc-500">No cameras configured. Add cameras first.</p>
              ) : (
                <div className="max-h-64 space-y-2 overflow-y-auto rounded-lg border border-zinc-800 p-3">
                  {cameras.map((cam) => (
                    <div key={cam.id} className="flex items-center gap-3">
                      <Checkbox
                        id={cam.id}
                        checked={selectedCameraIds.includes(cam.id)}
                        onCheckedChange={() => toggleCamera(cam.id)}
                        className="border-zinc-600 data-[state=checked]:border-sky-500 data-[state=checked]:bg-sky-500"
                      />
                      <label htmlFor={cam.id} className="flex-1 cursor-pointer text-sm text-zinc-300">
                        {cam.name}
                        <span className="ml-2 text-xs text-zinc-600">{cam.location}</span>
                      </label>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
          <SheetFooter className="mt-8">
            <Button variant="ghost" onClick={() => setSheetOpen(false)} className="text-zinc-400">
              Cancel
            </Button>
            <Button
              onClick={handleCreate}
              disabled={!name || createSession.isPending}
              className="bg-sky-500 text-white hover:bg-sky-400"
            >
              {createSession.isPending ? "Creating…" : "Create Session"}
            </Button>
          </SheetFooter>
        </SheetContent>
      </Sheet>
    </div>
  );
}
