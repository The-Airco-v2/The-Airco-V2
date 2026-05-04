import { Camera as CameraIcon, Plus, Trash2, Upload } from "lucide-react";
import { useRef, useState } from "react";
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

export default function CamerasPage() {
  const { data: cameras, isLoading, error } = useCameras();
  const createCamera = useCreateCamera();
  const deleteCamera = useDeleteCamera();

  const [sheetOpen, setSheetOpen] = useState(false);
  const [deleteId, setDeleteId] = useState<string | null>(null);
  const [form, setForm] = useState<CreateCameraPayload>(emptyForm);
  const [importing, setImporting] = useState(false);
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
        title="Cameras"
        description={cameras ? `${cameras.length} camera${cameras.length !== 1 ? "s" : ""} configured` : ""}
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
    </div>
  );
}
