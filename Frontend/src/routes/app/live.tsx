import { Maximize2, Wifi } from "lucide-react";
import { useState } from "react";
import { PageHeader } from "@/components/shared/PageHeader";
import { StatusBadge } from "@/components/shared/StatusBadge";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { useCameras } from "@/hooks/useCameras";
import type { Camera } from "@/types";

// go2rtc proxied at /webrtc/ in dev (Vite proxy → :1984). In
// production builds VITE_GO2RTC_URL points at the deployed go2rtc
// host (e.g. https://media.the-airco.net) and the path is used
// directly. stream.html handles WebRTC → MSE → HLS → MJPEG fallback.
const GO2RTC_BASE =
  ((import.meta as ImportMeta & { env?: Record<string, string | undefined> }).env
    ?.VITE_GO2RTC_URL?.replace(/\/$/, "")) || "/webrtc";

function playerUrl(streamName: string): string {
  return `${GO2RTC_BASE}/stream.html?src=${encodeURIComponent(streamName)}`;
}

function CameraTile({ camera, onClick }: { camera: Camera; onClick: () => void }) {
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onClick}
      onKeyDown={(e) => e.key === "Enter" && onClick()}
      className="group relative aspect-video cursor-pointer overflow-hidden rounded-xl border border-zinc-800 bg-zinc-950 transition-colors hover:border-zinc-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-500"
      aria-label={`Open live feed: ${camera.name}`}
    >
      {/* Live stream — pointer-events:none so clicks reach the parent div */}
      <iframe
        src={playerUrl(camera.stream_name)}
        className="h-full w-full border-0"
        style={{ pointerEvents: "none" }}
        allow="autoplay"
        title={camera.name}
      />

      {/* Expand hint on hover */}
      <div className="pointer-events-none absolute inset-0 flex items-center justify-center opacity-0 transition-opacity group-hover:opacity-100">
        <div className="rounded-full bg-black/60 p-3">
          <Maximize2 className="h-6 w-6 text-white" />
        </div>
      </div>

      {/* Camera name + status bar */}
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

function StreamModal({ camera, onClose }: { camera: Camera | null; onClose: () => void }) {
  return (
    <Dialog open={!!camera} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-5xl gap-0 border-zinc-800 bg-zinc-950 p-0">
        <DialogHeader className="sr-only">
          <DialogTitle>{camera ? `Live feed: ${camera.name}` : "Live feed"}</DialogTitle>
          <DialogDescription>
            {camera
              ? `Fullscreen live stream for ${camera.name}. Close the dialog to return to the camera grid.`
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

export default function LivePage() {
  const { data: cameras, isLoading, error } = useCameras();
  const [activeCamera, setActiveCamera] = useState<Camera | null>(null);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Live View"
        description={
          cameras
            ? `${cameras.length} camera${cameras.length !== 1 ? "s" : ""} configured`
            : "Real-time camera feeds"
        }
      />

      {error && (
        <div className="rounded-xl border border-red-900 bg-red-950/40 px-4 py-3 text-sm text-red-300">
          Failed to load cameras.
        </div>
      )}

      {isLoading ? (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="aspect-video w-full rounded-xl bg-zinc-800" />
          ))}
        </div>
      ) : !cameras?.length ? (
        <div className="flex flex-col items-center justify-center rounded-xl border border-zinc-800 bg-zinc-900 py-24">
          <Wifi className="mb-3 h-10 w-10 text-zinc-700" />
          <p className="text-sm text-zinc-500">No cameras configured</p>
          <p className="mt-1 text-xs text-zinc-600">Add cameras on the Cameras page to see live feeds here.</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {cameras.map((cam) => (
            <CameraTile key={cam.id} camera={cam} onClick={() => setActiveCamera(cam)} />
          ))}
        </div>
      )}

      <StreamModal camera={activeCamera} onClose={() => setActiveCamera(null)} />
    </div>
  );
}
