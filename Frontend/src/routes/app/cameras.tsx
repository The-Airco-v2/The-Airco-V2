import { Camera as CameraIcon, Maximize2, Plus, Trash2, Upload, Wifi } from "lucide-react";
import { useState, useRef } from "react";
import { PageHeader } from "@/components/shared/PageHeader";
import { StatusBadge } from "@/components/shared/StatusBadge";
import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Sheet, SheetContent, SheetFooter, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { useCameras, useCreateCamera, useDeleteCamera } from "@/hooks/useCameras";
import { toast } from "sonner";
import type { CreateCameraPayload } from "@/types";

const emptyForm: CreateCameraPayload = {
  name: "",
  rtsp_url: "",
  location: "",
  zone: "",
  is_entrance: false,
};

const GO2RTC_BASE = "/webrtc";

function playerUrl(streamName: string): string {
  return `${GO2RTC_BASE}/stream.html?src=${encodeURIComponent(streamName)}`;
}

function CameraTile({
  camera,
  onClick,
}: {
  camera: NonNullable<ReturnType<typeof useCameras>["data"]>[number];
  onClick: () => void;
}) {
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onClick}
      onKeyDown={(e) => e.key === "Enter" && onClick()}
      className="group relative aspect-video cursor-pointer overflow-hidden rounded-xl border border-zinc-800 bg-zinc-950 transition-colors hover:border-zinc-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-500"
      aria-label={`Open live feed: ${camera.name}`}
    >
      <iframe
        src={playerUrl(camera.stream_name)}
        className="h-full w-full border-0"
        style={{ pointerEvents: "none" }}
        allow="autoplay"
        title={camera.name}
      />

      <div className="pointer-events-none absolute inset-0 flex items-center justify-center opacity-0 transition-opacity group-hover:opacity-100">
        <div className="rounded-full bg-black/60 p-3">
          <Maximize2 className="h-6 w-6 text-white" />
        </div>
      </div>

      <div className="pointer-events-none absolute bottom-0 left-0 right-0 flex items-center justify-between bg-gradient-to-t from-black/80 to-transparent px-3 py-2">
        <p className="truncate text-sm font-medium text-white drop-shadow">{camera.name}</p>
        <StatusBadge
          status={camera.status === "online" ? "online" : "offline"}
          className="shrink-0 text-[10px]"
        />
      </div>
    </div>
  );
}

function StreamModal({ camera, onClose }: { camera: NonNullable<ReturnType<typeof useCameras>["data"]>[number] | null; onClose: () => void; }) {
  return (
    <Dialog open={!!camera} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-5xl gap-0 border-zinc-800 bg-zinc-950 p-0">
        <DialogHeader className="sr-only">
          <DialogTitle>{camera ? `Live feed: ${camera.name}` : "Live feed"}</DialogTitle>
          <DialogDescription>
            {camera
              ? `Fullscreen live stream for ${camera.name}. Close the dialog to return to Camera Manager.`
              : "Fullscreen live stream dialog."}
          </DialogDescription>
        </DialogHeader>
        <div className="relative aspect-video w-full bg-black">
          {camera && (
            <iframe
              src={playerUrl(camera.stream_name)}
              className="h-full w-full border-0"
              allow="autoplay"
              title={`Live feed: ${camera.name}`}
            />
          )}
        </div>
        <div className="flex items-center justify-between border-t border-zinc-800 px-5 py-3">
          <div>
            <p className="font-medium text-zinc-100">{camera?.name}</p>
            <p className="mt-0.5 text-xs text-zinc-500">
              {camera?.location && camera?.zone
                ? `${camera.location} · ${camera.zone}`
                : camera?.location || camera?.zone || "No location set"}
            </p>
          </div>
          <StatusBadge status={camera?.status === "online" ? "online" : "offline"} />
        </div>
      </DialogContent>
    </Dialog>
  );
}

export default function CamerasPage() {
  const { data: cameras, isLoading, error } = useCameras();
  const createCamera = useCreateCamera();
  const deleteCamera = useDeleteCamera();

  const [sheetOpen, setSheetOpen] = useState(false);
  const [deleteId, setDeleteId] = useState<string | null>(null);
  const [form, setForm] = useState<CreateCameraPayload>(emptyForm);
  const [importing, setImporting] = useState(false);
  const [activeCamera, setActiveCamera] = useState<NonNullable<ReturnType<typeof useCameras>["data"]>[number] | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleCreate = async () => {
    createCamera.mutate(form, {
      onSuccess: () => {
        toast.success("Camera added");
        setSheetOpen(false);
        setForm(emptyForm);
      },
      onError: () => toast.error("Failed to add camera"),
    });
  };

  const handleImport = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!fileInputRef.current) return;
    fileInputRef.current.value = "";
    if (!file) return;

    let parsed: { cameras?: Record<string, unknown>[] };
    try {
      parsed = JSON.parse(await file.text());
    } catch {
      toast.error("Invalid JSON file");
      return;
    }

    const rows = parsed?.cameras;
    if (!Array.isArray(rows) || rows.length === 0) {
      toast.error("No cameras found in JSON (expected { cameras: [...] })");
      return;
    }

    setImporting(true);
    let added = 0;
    let failed = 0;
    for (const row of rows) {
      const payload: CreateCameraPayload = {
        name: String(row.name ?? ""),
        rtsp_url: String(row.rtsp_link ?? row.url ?? row.rtsp_url ?? ""),
        location: row.location ? String(row.location) : "",
        zone: row.zone ? String(row.zone) : "",
        is_entrance: Boolean(row.entrance_monitor ?? row.is_entrance ?? false),
      };
      if (!payload.name || !payload.rtsp_url) { failed++; continue; }
      try {
        await new Promise<void>((resolve, reject) =>
          createCamera.mutate(payload, { onSuccess: () => resolve(), onError: reject })
        );
        added++;
      } catch {
        failed++;
      }
    }
    setImporting(false);
    if (failed === 0) {
      toast.success(`Imported ${added} camera${added !== 1 ? "s" : ""}`);
    } else {
      toast.warning(`Imported ${added}, failed ${failed}`);
    }
  };

  const handleDelete = () => {
    if (!deleteId) {
      return;
    }
    deleteCamera.mutate(deleteId, {
      onSuccess: () => {
        toast.success("Camera removed");
        setDeleteId(null);
      },
      onError: () => toast.error("Failed to remove camera"),
    });
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title="Camera Manager"
        description={cameras ? `${cameras.length} camera${cameras.length !== 1 ? "s" : ""} configured` : "Manage cameras and watch live feeds in one place."}
        actions={
          <div className="flex items-center gap-2">
            <input
              ref={fileInputRef}
              type="file"
              accept=".json"
              className="hidden"
              onChange={handleImport}
            />
            <Button
              size="sm"
              variant="outline"
              className="gap-1.5 border-zinc-700 text-zinc-300 hover:bg-zinc-800"
              onClick={() => fileInputRef.current?.click()}
              disabled={importing}
            >
              <Upload className="h-4 w-4" />
              {importing ? "Importing…" : "Import JSON"}
            </Button>
            <Button
              onClick={() => setSheetOpen(true)}
              className="gap-1.5 bg-sky-500 text-white hover:bg-sky-400"
              size="sm"
            >
              <Plus className="h-4 w-4" /> Add Camera
            </Button>
          </div>
        }
      />

      <div className="rounded-xl border border-zinc-800 bg-zinc-900 p-4">
        <div className="mb-4 flex items-center justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold text-zinc-100">Live Feeds</h2>
            <p className="text-xs text-zinc-500">Preview any camera stream directly from Camera Manager.</p>
          </div>
          <div className="flex items-center gap-2 text-xs text-zinc-500">
            <Wifi className="h-3.5 w-3.5" />
            Real-time view
          </div>
        </div>

        {error && (
          <Alert variant="destructive" className="mb-4 border-red-800 bg-red-950/40">
            <AlertDescription className="text-red-300">Failed to load live cameras.</AlertDescription>
          </Alert>
        )}

        {isLoading ? (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="aspect-video w-full rounded-xl bg-zinc-800" />
            ))}
          </div>
        ) : !cameras?.length ? (
          <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-zinc-800 bg-zinc-950 py-16">
            <Wifi className="mb-3 h-10 w-10 text-zinc-700" />
            <p className="text-sm text-zinc-500">No cameras configured</p>
            <p className="mt-1 text-xs text-zinc-600">Add cameras below to see live feeds here.</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {cameras.map((cam) => (
              <CameraTile key={cam.id} camera={cam} onClick={() => setActiveCamera(cam)} />
            ))}
          </div>
        )}
      </div>

      {error && (
        <Alert variant="destructive" className="border-red-800 bg-red-950/40">
          <AlertDescription className="text-red-300">Failed to load cameras.</AlertDescription>
        </Alert>
      )}

      <div className="overflow-hidden rounded-xl border border-zinc-800 bg-zinc-900">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-zinc-800">
              {["Name", "Location", "Zone", "RTSP URL", "Status", "Last Seen", ""].map((h) => (
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
                  {Array.from({ length: 7 }).map((__, j) => (
                    <td key={j} className="px-5 py-4">
                      <Skeleton className="h-4 w-full bg-zinc-800" />
                    </td>
                  ))}
                </tr>
              ))
            ) : !cameras?.length ? (
              <tr>
                <td colSpan={7} className="py-16 text-center">
                  <CameraIcon className="mx-auto mb-3 h-8 w-8 text-zinc-700" />
                  <p className="text-sm text-zinc-500">No cameras configured</p>
                </td>
              </tr>
            ) : (
              cameras.map((cam) => (
                <tr key={cam.id} className="border-b border-zinc-800/50 transition-colors hover:bg-zinc-800/20">
                  <td className="px-5 py-3.5 font-medium text-zinc-100">{cam.name}</td>
                  <td className="px-5 py-3.5 text-zinc-400">{cam.location || "—"}</td>
                  <td className="px-5 py-3.5 text-zinc-400">{cam.zone || "—"}</td>
                  <td className="max-w-xs px-5 py-3.5">
                    <span className="block truncate font-mono text-xs text-zinc-500" title={cam.rtsp_url}>
                      {cam.rtsp_url.replace(/rtsp:\/\/[^@]+@/, "rtsp://***@")}
                    </span>
                  </td>
                  <td className="px-5 py-3.5">
                    <StatusBadge
                      status={
                        cam.status === "online" ? "online" : cam.status === "offline" ? "offline" : "unknown"
                      }
                    />
                  </td>
                  <td className="px-5 py-3.5 text-xs text-zinc-500">
                    {cam.last_seen ? new Date(cam.last_seen).toLocaleString() : "—"}
                  </td>
                  <td className="px-5 py-3.5">
                    <Button
                      size="icon"
                      variant="ghost"
                      className="h-7 w-7 text-zinc-500 hover:bg-red-500/10 hover:text-red-400"
                      onClick={() => setDeleteId(cam.id)}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
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
            <SheetTitle className="text-zinc-50">Add Camera</SheetTitle>
          </SheetHeader>
          <div className="mt-6 space-y-4">
            {(["name", "rtsp_url", "location"] as const).map((field) => (
              <div key={field} className="space-y-1.5">
                <Label className="capitalize text-zinc-300">{field.replace("_", " ")}</Label>
                <Input
                  value={form[field]}
                  onChange={(e) => setForm((f) => ({ ...f, [field]: e.target.value }))}
                  className="border-zinc-700 bg-zinc-800 text-zinc-50 focus-visible:ring-sky-500"
                  placeholder={field === "rtsp_url" ? "rtsp://..." : ""}
                />
              </div>
            ))}
            <div className="space-y-1.5">
              <Label className="text-zinc-300">Zone / Room</Label>
              <Input
                value={form.zone}
                onChange={(e) => setForm((f) => ({ ...f, zone: e.target.value }))}
                className="border-zinc-700 bg-zinc-800 text-zinc-50 focus-visible:ring-sky-500"
                placeholder="e.g. Reception, Office Floor 2"
              />
              <p className="text-xs text-zinc-500">
                Cameras sharing the same zone are treated as same-space — the system merges identities across angles automatically.
              </p>
            </div>
            <div className="flex items-center gap-3">
              <input
                id="entrance"
                type="checkbox"
                checked={form.is_entrance}
                onChange={(e) => setForm((f) => ({ ...f, is_entrance: e.target.checked }))}
                className="h-4 w-4 rounded border-zinc-600 bg-zinc-800 accent-sky-500"
              />
              <Label htmlFor="entrance" className="text-zinc-300">
                Entrance camera
              </Label>
            </div>
          </div>
          <SheetFooter className="mt-8">
            <Button variant="ghost" onClick={() => setSheetOpen(false)} className="text-zinc-400">
              Cancel
            </Button>
            <Button
              onClick={handleCreate}
              disabled={!form.name || !form.rtsp_url || createCamera.isPending}
              className="bg-sky-500 text-white hover:bg-sky-400"
            >
              {createCamera.isPending ? "Adding…" : "Add Camera"}
            </Button>
          </SheetFooter>
        </SheetContent>
      </Sheet>

      <AlertDialog open={!!deleteId} onOpenChange={(o) => !o && setDeleteId(null)}>
        <AlertDialogContent className="border-zinc-800 bg-zinc-900">
          <AlertDialogHeader>
            <AlertDialogTitle className="text-zinc-50">Remove camera?</AlertDialogTitle>
            <AlertDialogDescription className="text-zinc-400">
              This will permanently remove the camera from your workspace.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="border-zinc-700 bg-transparent text-zinc-300 hover:bg-zinc-800">
              Cancel
            </AlertDialogCancel>
            <AlertDialogAction onClick={handleDelete} className="bg-red-600 text-white hover:bg-red-500">
              Remove
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <StreamModal camera={activeCamera} onClose={() => setActiveCamera(null)} />
    </div>
  );
}
