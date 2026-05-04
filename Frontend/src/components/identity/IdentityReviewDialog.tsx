import { useMemo, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useAssignEmployeeIdentity, useMergeUnknownPersons } from "@/hooks/useIdentityReviews";
import { useEmployees } from "@/hooks/useEmployees";
import type { UnknownPersonSummary } from "@/types";

function CandidateCard({
  person,
  selected,
  onToggle,
}: {
  person: UnknownPersonSummary;
  selected: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onToggle}
      className={`flex w-full items-center gap-3 rounded-md border px-3 py-2 text-left transition ${
        selected
          ? "border-sky-500 bg-sky-500/10"
          : "border-zinc-800 bg-zinc-950 hover:bg-zinc-900"
      }`}
    >
      <div className="h-14 w-10 shrink-0 overflow-hidden rounded border border-zinc-800 bg-zinc-900">
        {person.best_thumbnail_url ? (
          <img src={person.best_thumbnail_url} alt={person.display_name} className="h-full w-full object-cover" />
        ) : null}
      </div>
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium text-zinc-100">{person.display_name}</p>
        <p className="truncate text-xs text-zinc-500">
          {[person.current_camera, person.current_zone].filter(Boolean).join(" · ") || "Location pending"}
        </p>
        <p className="mt-1 text-[11px] text-zinc-400">
          {Math.round(person.face_confidence * 100)}% face confidence · {Math.round(person.dwell_seconds / 60)}m dwell
        </p>
      </div>
    </button>
  );
}

export function IdentityReviewDialog({
  open,
  onOpenChange,
  person,
  candidates,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  person: UnknownPersonSummary;
  candidates: UnknownPersonSummary[];
}) {
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [reason, setReason] = useState("");
  const [employeeId, setEmployeeId] = useState("");
  const mergeMutation = useMergeUnknownPersons();
  const assignMutation = useAssignEmployeeIdentity();
  const { data: employees } = useEmployees();

  const candidateOptions = useMemo(
    () => candidates.filter((candidate) => candidate.person_id !== person.person_id),
    [candidates, person.person_id],
  );

  const pending = mergeMutation.isPending || assignMutation.isPending;

  const reset = () => {
    setSelectedIds([]);
    setReason("");
    setEmployeeId("");
  };

  const handleMerge = async () => {
    if (!selectedIds.length) {
      toast.error("Select at least one person to merge");
      return;
    }
    try {
      await mergeMutation.mutateAsync({
        source_person_id: person.person_id,
        target_person_ids: selectedIds,
        reason: reason || undefined,
      });
      toast.success("Merged identities");
      reset();
      onOpenChange(false);
    } catch {
      toast.error("Could not merge identities");
    }
  };

  const handleAssign = async () => {
    if (!employeeId) {
      toast.error("Choose an employee");
      return;
    }
    try {
      await assignMutation.mutateAsync({
        source_person_id: person.person_id,
        employee_id: employeeId,
        reason: reason || undefined,
      });
      toast.success("Assigned identity to employee");
      reset();
      onOpenChange(false);
    } catch {
      toast.error("Could not assign employee");
    }
  };

  return (
    <Dialog open={open} onOpenChange={(next) => { if (!next) reset(); onOpenChange(next); }}>
      <DialogContent className="max-w-3xl border-zinc-800 bg-zinc-950 text-zinc-100">
        <DialogHeader>
          <DialogTitle>Review identity for {person.display_name}</DialogTitle>
          <DialogDescription className="text-zinc-400">
            Merge this unknown person with other unknown sightings or assign them directly to a known employee.
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-6 lg:grid-cols-[1.3fr_1fr]">
          <div className="space-y-3">
            <div>
              <p className="text-xs font-semibold uppercase tracking-widest text-zinc-500">Merge with unknown persons</p>
              <div className="mt-3 max-h-80 space-y-2 overflow-y-auto pr-1">
                {candidateOptions.length ? (
                  candidateOptions.map((candidate) => (
                    <CandidateCard
                      key={candidate.person_id}
                      person={candidate}
                      selected={selectedIds.includes(candidate.person_id)}
                      onToggle={() =>
                        setSelectedIds((current) =>
                          current.includes(candidate.person_id)
                            ? current.filter((id) => id !== candidate.person_id)
                            : [...current, candidate.person_id],
                        )
                      }
                    />
                  ))
                ) : (
                  <p className="rounded-md border border-zinc-800 bg-zinc-900/60 px-3 py-4 text-sm text-zinc-500">
                    No other unknown persons are available in this session right now.
                  </p>
                )}
              </div>
            </div>
          </div>

          <div className="space-y-4">
            <div className="rounded-md border border-zinc-800 bg-zinc-900/70 p-4">
              <p className="text-xs font-semibold uppercase tracking-widest text-zinc-500">Assign to employee</p>
              <select
                value={employeeId}
                onChange={(event) => setEmployeeId(event.target.value)}
                className="mt-3 w-full rounded-md border border-zinc-800 bg-zinc-950 px-3 py-2 text-sm text-zinc-100"
              >
                <option value="">Select employee</option>
                {(employees ?? []).map((employee) => (
                  <option key={employee.id} value={employee.id}>
                    {employee.name}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <p className="text-xs font-semibold uppercase tracking-widest text-zinc-500">Reason</p>
              <textarea
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                placeholder="Why are these the same person?"
                className="mt-3 min-h-28 w-full rounded-md border border-zinc-800 bg-zinc-950 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600"
              />
            </div>
          </div>
        </div>

        <DialogFooter className="gap-2 sm:justify-between">
          <Button variant="ghost" onClick={() => onOpenChange(false)} className="text-zinc-400">
            Cancel
          </Button>
          <div className="flex gap-2">
            <Button
              variant="secondary"
              onClick={handleMerge}
              disabled={pending || !selectedIds.length}
              className="bg-zinc-800 text-zinc-100 hover:bg-zinc-700"
            >
              Merge selected
            </Button>
            <Button onClick={handleAssign} disabled={pending || !employeeId} className="bg-sky-600 text-white hover:bg-sky-500">
              Assign to employee
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
