import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, Clock3, GitMerge, History, ImageOff, Link2, ShieldCheck, UserCheck } from "lucide-react";
import { useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { PageHeader } from "@/components/shared/PageHeader";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useEmployees } from "@/hooks/useEmployees";
import {
  useIdentityReviewHistory,
  useIdentityReviewItem,
  useIdentityReviewQueue,
} from "@/hooks/useIdentityReviewPage";
import {
  useAssignEmployeeIdentity,
  useMergeUnknownPersons,
  useUndoIdentityReview,
} from "@/hooks/useIdentityReviews";
import { useOverviewToday } from "@/hooks/useSessions";
import { cn } from "@/lib/utils";
import type {
  IdentityReviewHistoryItem,
  IdentityReviewItemDetail,
  IdentityReviewQueueItem,
  IdentityReviewScope,
  IdentityReviewWorkspaceCandidate,
} from "@/types";

function formatTimestamp(value: string | null | undefined) {
  if (!value) {
    return "—";
  }
  return new Date(value).toLocaleString([], {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function reasonTagLabel(tag: string) {
  switch (tag) {
    case "same_session_unknown":
      return "Same session";
    case "review_identity_evidence":
      return "Needs review";
    case "anonymous_cluster_match":
      return "Cluster candidate";
    case "cross_session_review":
      return "Across sessions";
    default:
      return tag.replace(/_/g, " ");
  }
}

function ScopeToggle({
  scope,
  onChange,
}: {
  scope: IdentityReviewScope;
  onChange: (scope: IdentityReviewScope) => void;
}) {
  return (
    <div className="inline-flex rounded-lg border border-zinc-800 bg-zinc-900 p-1">
      {[
        { id: "active_session", label: "Active Session" },
        { id: "cross_session", label: "Across Sessions" },
      ].map((option) => (
        <button
          key={option.id}
          type="button"
          onClick={() => onChange(option.id as IdentityReviewScope)}
          className={cn(
            "rounded-md px-3 py-2 text-sm font-medium transition-colors",
            scope === option.id
              ? "bg-sky-500 text-white"
              : "text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100",
          )}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

function QueueCard({
  item,
  active,
  onSelect,
}: {
  item: IdentityReviewQueueItem;
  active: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      className={cn(
        "w-full rounded-xl border p-4 text-left transition-colors",
        active
          ? "border-sky-500/50 bg-sky-500/10"
          : "border-zinc-800 bg-zinc-900/70 hover:border-zinc-700 hover:bg-zinc-900",
      )}
    >
      <div className="flex items-start gap-3">
        <div className="h-16 w-12 shrink-0 overflow-hidden rounded-md border border-zinc-800 bg-zinc-950">
          {item.representative_thumbnail_url ? (
            <img
              src={item.representative_thumbnail_url}
              alt={item.display_name ?? "Identity review item"}
              className="h-full w-full object-cover"
            />
          ) : (
            <div className="flex h-full w-full items-center justify-center text-zinc-600">
              <ImageOff className="h-4 w-4" />
            </div>
          )}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-3">
            <p className="truncate text-sm font-semibold text-zinc-100">
              {item.display_name ?? "Unknown identity"}
            </p>
            <span className="rounded-full bg-zinc-800 px-2 py-0.5 text-[11px] uppercase tracking-wide text-zinc-400">
              {Math.round(item.confidence * 100)}%
            </span>
          </div>
          <p className="mt-1 text-xs text-zinc-500">
            {item.session_name ?? "Cross-session review"} · {item.candidate_count} candidate
            {item.candidate_count === 1 ? "" : "s"}
          </p>
          <div className="mt-3 flex flex-wrap gap-1.5">
            {item.reason_tags.map((tag) => (
              <span
                key={tag}
                className="rounded-full border border-zinc-800 bg-zinc-950 px-2 py-0.5 text-[11px] text-zinc-400"
              >
                {reasonTagLabel(tag)}
              </span>
            ))}
          </div>
          <p className="mt-3 text-[11px] text-zinc-500">Last seen {formatTimestamp(item.last_seen_at)}</p>
        </div>
      </div>
    </button>
  );
}

function CandidateTile({
  candidate,
  selected,
  onToggle,
}: {
  candidate: IdentityReviewWorkspaceCandidate;
  selected: boolean;
  onToggle?: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onToggle}
      disabled={!onToggle}
      className={cn(
        "flex w-full items-start gap-3 rounded-lg border p-3 text-left transition-colors",
        selected ? "border-sky-500/50 bg-sky-500/10" : "border-zinc-800 bg-zinc-900/70",
        onToggle ? "hover:border-zinc-700" : "cursor-default",
      )}
    >
      <div className="h-16 w-12 shrink-0 overflow-hidden rounded-md border border-zinc-800 bg-zinc-950">
        {candidate.best_thumbnail_url ? (
          <img
            src={candidate.best_thumbnail_url}
            alt={candidate.display_name ?? "Candidate identity"}
            className="h-full w-full object-cover"
          />
        ) : (
          <div className="flex h-full w-full items-center justify-center text-zinc-600">
            <ImageOff className="h-4 w-4" />
          </div>
        )}
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center justify-between gap-3">
          <p className="truncate text-sm font-medium text-zinc-100">
            {candidate.display_name ?? "Unnamed candidate"}
          </p>
          <span className="text-[11px] text-zinc-400">{Math.round(candidate.confidence * 100)}%</span>
        </div>
        <p className="mt-1 text-xs text-zinc-500">
          {candidate.employee_id
            ? `Employee ${candidate.employee_id}`
            : candidate.cluster_id
              ? `Cluster ${candidate.cluster_id.slice(0, 8)}`
              : candidate.person_id
                ? `Person ${candidate.person_id.slice(0, 8)}`
                : "Identity candidate"}
        </p>
        <div className="mt-2 flex flex-wrap gap-1.5">
          {candidate.reason_tags.map((tag) => (
            <span
              key={tag}
              className="rounded-full border border-zinc-800 bg-zinc-950 px-2 py-0.5 text-[11px] text-zinc-400"
            >
              {reasonTagLabel(tag)}
            </span>
          ))}
        </div>
      </div>
    </button>
  );
}

function HistoryList({ items }: { items: IdentityReviewHistoryItem[] }) {
  if (!items.length) {
    return (
      <p className="rounded-lg border border-zinc-800 bg-zinc-900/60 px-3 py-4 text-sm text-zinc-500">
        No review history yet for this scope.
      </p>
    );
  }

  return (
    <div className="space-y-2">
      {items.slice(0, 5).map((item) => (
        <div key={item.review_id} className="rounded-lg border border-zinc-800 bg-zinc-900/70 p-3">
          <div className="flex items-center justify-between gap-3">
            <p className="text-sm font-medium text-zinc-100">{item.review_type.replace(/_/g, " ")}</p>
            <span className="text-[11px] uppercase tracking-wide text-zinc-500">{item.decision}</span>
          </div>
          <p className="mt-1 text-xs text-zinc-500">{formatTimestamp(item.created_at)}</p>
          {item.reason ? <p className="mt-2 text-xs text-zinc-400">{item.reason}</p> : null}
        </div>
      ))}
    </div>
  );
}

function WorkspaceSkeleton() {
  return (
    <div className="space-y-4">
      <Skeleton className="h-36 w-full rounded-xl bg-zinc-900" />
      <Skeleton className="h-28 w-full rounded-xl bg-zinc-900" />
      <Skeleton className="h-44 w-full rounded-xl bg-zinc-900" />
    </div>
  );
}

function Lightbox({
  src,
  alt,
  onClose,
}: {
  src: string;
  alt: string;
  onClose: () => void;
}) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/90 p-4"
      onClick={onClose}
    >
      <img
        src={src}
        alt={alt}
        className="max-h-full max-w-full object-contain"
        onClick={(event) => event.stopPropagation()}
      />
      <button
        type="button"
        onClick={onClose}
        className="absolute right-4 top-4 rounded-full bg-zinc-900 p-2 text-zinc-300 hover:bg-zinc-800"
      >
        ✕
      </button>
    </div>
  );
}

export default function IdentityReviewPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const scope: IdentityReviewScope =
    searchParams.get("scope") === "cross_session" ? "cross_session" : "active_session";
  const selectedItemId = searchParams.get("item");
  const [selectedCandidateIds, setSelectedCandidateIds] = useState<string[]>([]);
  const [employeeId, setEmployeeId] = useState("");
  const [reason, setReason] = useState("");
  const [lightbox, setLightbox] = useState<{ src: string; alt: string } | null>(null);

  const setScope = (next: IdentityReviewScope) => {
    setSearchParams((prev) => {
      const p = new URLSearchParams(prev);
      p.set("scope", next);
      p.delete("item");
      return p;
    }, { replace: true });
  };

  const setSelectedItemId = (itemId: string | null) => {
    setSearchParams((prev) => {
      const p = new URLSearchParams(prev);
      if (itemId) {
        p.set("item", itemId);
      } else {
        p.delete("item");
      }
      return p;
    }, { replace: true });
  };

  const { data: overview } = useOverviewToday();
  const activeSessionId = overview?.session?.id ?? null;
  const queueQuery = useIdentityReviewQueue(scope, scope === "active_session" ? activeSessionId : null);
  const queueItems = queueQuery.data?.items ?? [];
  const detailQuery = useIdentityReviewItem(selectedItemId);
  const historyQuery = useIdentityReviewHistory();
  const { data: employees } = useEmployees();

  const mergeMutation = useMergeUnknownPersons();
  const assignMutation = useAssignEmployeeIdentity();
  const undoMutation = useUndoIdentityReview();

  // Auto-select first queue item if none selected or current selection not in queue
  useEffect(() => {
    if (!queueItems.length) {
      if (selectedItemId) setSelectedItemId(null);
      return;
    }
    if (!selectedItemId || !queueItems.some((item) => item.review_item_id === selectedItemId)) {
      setSelectedItemId(queueItems[0]?.review_item_id ?? null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [queueItems.length, queueItems.map((i) => i.review_item_id).join(",")]);

  useEffect(() => {
    setSelectedCandidateIds([]);
    setEmployeeId("");
    setReason("");
  }, [selectedItemId]);

  const detail = detailQuery.data;
  const mergeCandidates = useMemo(
    () => (detail?.candidates ?? []).filter((candidate) => !!candidate.person_id),
    [detail],
  );
  const historyItems = useMemo(() => {
    const sourcePersonId = detail?.source.person_id;
    if (!sourcePersonId) {
      return historyQuery.data?.items ?? [];
    }
    return (historyQuery.data?.items ?? []).filter(
      (item) =>
        item.source_session_person_id === sourcePersonId ||
        item.target_session_person_id === sourcePersonId,
    );
  }, [detail, historyQuery.data?.items]);

  const pending = mergeMutation.isPending || assignMutation.isPending || undoMutation.isPending;

  const toggleCandidate = (candidateId: string) => {
    setSelectedCandidateIds((current) =>
      current.includes(candidateId)
        ? current.filter((id) => id !== candidateId)
        : [...current, candidateId],
    );
  };

  const handleMerge = async () => {
    const source = detail?.source;
    if (!source?.person_id || !selectedCandidateIds.length) {
      toast.error("Select at least one candidate to merge");
      return;
    }
    try {
      await mergeMutation.mutateAsync({
        source_person_id: source.person_id,
        target_person_ids: selectedCandidateIds,
        reason: reason || undefined,
      });
      toast.success("Merged reviewed identities");
      await Promise.all([queueQuery.refetch(), detailQuery.refetch(), historyQuery.refetch()]);
      setSelectedCandidateIds([]);
      setReason("");
    } catch {
      toast.error("Could not merge identities");
    }
  };

  const handleAssign = async () => {
    const source = detail?.source;
    if (!source?.person_id || !employeeId) {
      toast.error("Choose an employee to assign");
      return;
    }
    try {
      await assignMutation.mutateAsync({
        source_person_id: source.person_id,
        employee_id: employeeId,
        reason: reason || undefined,
      });
      toast.success("Assigned reviewed identity to employee");
      await Promise.all([queueQuery.refetch(), detailQuery.refetch(), historyQuery.refetch()]);
      setEmployeeId("");
      setReason("");
    } catch {
      toast.error("Could not assign employee");
    }
  };

  const latestReviewId = historyItems[0]?.review_id;

  const handleUndo = async () => {
    if (!latestReviewId) {
      toast.error("No review action available to undo");
      return;
    }
    try {
      await undoMutation.mutateAsync({
        reviewId: latestReviewId,
        payload: { reason: reason || "Operator reversed the latest review decision" },
      });
      toast.success("Reverted latest identity review");
      await Promise.all([queueQuery.refetch(), detailQuery.refetch(), historyQuery.refetch()]);
    } catch {
      toast.error("Could not undo review");
    }
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title="Identity Review"
        description="Review live duplicate unknowns and cross-session identity candidates with evidence-first decisions."
        actions={<ScopeToggle scope={scope} onChange={setScope} />}
      />

      <div className="grid gap-6 xl:grid-cols-[360px_minmax(0,1fr)]">
        <Card className="border-zinc-800 bg-zinc-950 text-zinc-100">
          <CardHeader className="space-y-3">
            <div className="flex items-center justify-between gap-3">
              <CardTitle className="text-base font-semibold">
                {scope === "active_session" ? "Active Session Queue" : "Cross-Session Queue"}
              </CardTitle>
              <span className="rounded-full bg-zinc-900 px-2.5 py-1 text-xs text-zinc-400">
                {queueItems.length} item{queueItems.length === 1 ? "" : "s"}
              </span>
            </div>
            {scope === "active_session" ? (
              <p className="text-sm text-zinc-500">
                {activeSessionId
                  ? `Using active session ${overview?.session?.name ?? activeSessionId.slice(0, 8)}`
                  : "No running session detected yet."}
              </p>
            ) : (
              <p className="text-sm text-zinc-500">
                Reviewing cluster candidates and unresolved identity ambiguity across sessions.
              </p>
            )}
          </CardHeader>
          <CardContent className="space-y-3">
            {queueQuery.isLoading ? (
              <>
                <Skeleton className="h-28 w-full rounded-xl bg-zinc-900" />
                <Skeleton className="h-28 w-full rounded-xl bg-zinc-900" />
                <Skeleton className="h-28 w-full rounded-xl bg-zinc-900" />
              </>
            ) : queueItems.length ? (
              queueItems.map((item) => (
                <div key={item.review_item_id} className="relative">
                  <QueueCard
                    item={item}
                    active={item.review_item_id === selectedItemId}
                    onSelect={() => setSelectedItemId(item.review_item_id)}
                  />
                  {item.representative_thumbnail_url ? (
                    <button
                      type="button"
                      onClick={(event) => {
                        event.stopPropagation();
                        setLightbox({
                          src: item.representative_thumbnail_url!,
                          alt: item.display_name ?? "Identity review item",
                        });
                      }}
                      className="absolute left-5 top-5 rounded-md bg-black/65 px-2 py-1 text-[11px] text-white hover:bg-black/80"
                    >
                      Enlarge
                    </button>
                  ) : null}
                </div>
              ))
            ) : (
              <div className="rounded-xl border border-dashed border-zinc-800 bg-zinc-900/60 px-4 py-10 text-center">
                <ShieldCheck className="mx-auto h-8 w-8 text-zinc-600" />
                <p className="mt-3 text-sm font-medium text-zinc-200">No review suggestions</p>
                <p className="mt-1 text-sm text-zinc-500">
                  {scope === "active_session"
                    ? "Start or continue a live session to generate same-session merge candidates."
                    : "Cross-session cluster candidates will appear here when the identity engine detects ambiguity."}
                </p>
              </div>
            )}
          </CardContent>
        </Card>

        <div className="space-y-6">
          {detailQuery.isLoading ? (
            <WorkspaceSkeleton />
          ) : detail ? (
            <>
              <Card className="border-zinc-800 bg-zinc-950 text-zinc-100">
                <CardHeader>
                  <div className="flex items-center gap-4">
                    <div className="h-20 w-16 overflow-hidden rounded-lg border border-zinc-800 bg-zinc-900">
                      {detail.source.best_thumbnail_url ? (
                        <button
                          type="button"
                          onClick={() =>
                            setLightbox({
                              src: detail.source.best_thumbnail_url!,
                              alt: detail.source.display_name ?? "Source identity",
                            })
                          }
                          className="h-full w-full"
                          title="Click to enlarge"
                        >
                          <img
                            src={detail.source.best_thumbnail_url}
                            alt={detail.source.display_name ?? "Source identity"}
                            className="h-full w-full object-cover transition-opacity hover:opacity-90"
                          />
                        </button>
                      ) : (
                        <div className="flex h-full w-full items-center justify-center text-zinc-600">
                          <ImageOff className="h-5 w-5" />
                        </div>
                      )}
                    </div>
                    <div className="min-w-0 flex-1">
                      <CardTitle className="text-lg font-semibold">
                        {detail.source.display_name ?? "Unknown identity"}
                      </CardTitle>
                      <p className="mt-1 text-sm text-zinc-500">
                        {detail.source.session_name ?? "Cross-session candidate"} · {Math.round(detail.source.confidence * 100)}% confidence
                      </p>
                      <div className="mt-3 flex flex-wrap gap-2 text-xs text-zinc-400">
                        <span className="inline-flex items-center gap-1 rounded-full border border-zinc-800 bg-zinc-900 px-2.5 py-1">
                          <Clock3 className="h-3.5 w-3.5" />
                          First seen {formatTimestamp(detail.source.first_seen_at)}
                        </span>
                        <span className="inline-flex items-center gap-1 rounded-full border border-zinc-800 bg-zinc-900 px-2.5 py-1">
                          <History className="h-3.5 w-3.5" />
                          Last seen {formatTimestamp(detail.source.last_seen_at)}
                        </span>
                      </div>
                      {detail.source.best_thumbnail_url ? (
                        <button
                          type="button"
                          onClick={() =>
                            setLightbox({
                              src: detail.source.best_thumbnail_url!,
                              alt: detail.source.display_name ?? "Source identity",
                            })
                          }
                          className="mt-3 text-xs text-sky-400 hover:text-sky-300"
                        >
                          Enlarge image ↗
                        </button>
                      ) : null}
                    </div>
                  </div>
                </CardHeader>
                <CardContent className="space-y-5">
                  <div>
                    <p className="text-xs font-semibold uppercase tracking-widest text-zinc-500">Reason tags</p>
                    <div className="mt-3 flex flex-wrap gap-2">
                      {detail.source.reason_tags.map((tag) => (
                        <span
                          key={tag}
                          className="rounded-full border border-zinc-800 bg-zinc-900 px-2.5 py-1 text-xs text-zinc-400"
                        >
                          {reasonTagLabel(tag)}
                        </span>
                      ))}
                    </div>
                  </div>

                  <div>
                    <p className="text-xs font-semibold uppercase tracking-widest text-zinc-500">Candidate comparisons</p>
                    <div className="mt-3 space-y-3">
                      {detail.candidates.length ? (
                        detail.candidates.map((candidate) => {
                          const candidateKey =
                            candidate.person_id ?? candidate.cluster_id ?? candidate.display_name ?? "candidate";
                          return (
                            <div key={candidateKey} className="space-y-1.5">
                              <CandidateTile
                                candidate={candidate}
                                selected={candidate.person_id ? selectedCandidateIds.includes(candidate.person_id) : false}
                                onToggle={candidate.person_id ? () => toggleCandidate(candidate.person_id!) : undefined}
                              />
                              {candidate.best_thumbnail_url ? (
                                <button
                                  type="button"
                                  onClick={() =>
                                    setLightbox({
                                      src: candidate.best_thumbnail_url!,
                                      alt: candidate.display_name ?? "Candidate identity",
                                    })
                                  }
                                  className="text-xs text-sky-400 hover:text-sky-300"
                                >
                                  Enlarge candidate image ↗
                                </button>
                              ) : null}
                            </div>
                          );
                        })
                      ) : (
                        <p className="rounded-lg border border-zinc-800 bg-zinc-900/60 px-3 py-4 text-sm text-zinc-500">
                          No comparison candidates are available for this review item yet.
                        </p>
                      )}
                    </div>
                  </div>

                  <div className="grid gap-4 lg:grid-cols-[1.2fr_0.8fr]">
                    <div className="rounded-xl border border-zinc-800 bg-zinc-900/70 p-4">
                      <p className="text-xs font-semibold uppercase tracking-widest text-zinc-500">Review note</p>
                      <textarea
                        value={reason}
                        onChange={(event) => setReason(event.target.value)}
                        placeholder="Why should this identity be merged or assigned?"
                        className="mt-3 min-h-32 w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600"
                      />
                    </div>
                    <div className="rounded-xl border border-zinc-800 bg-zinc-900/70 p-4">
                      <p className="text-xs font-semibold uppercase tracking-widest text-zinc-500">Assign to employee</p>
                      <select
                        value={employeeId}
                        onChange={(event) => setEmployeeId(event.target.value)}
                        className="mt-3 w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-sm text-zinc-100"
                      >
                        <option value="">Select employee</option>
                        {(employees ?? []).map((employee) => (
                          <option key={employee.id} value={employee.id}>
                            {employee.name}
                          </option>
                        ))}
                      </select>
                      <div className="mt-4 grid gap-2">
                        <Button
                          onClick={handleMerge}
                          disabled={pending || !selectedCandidateIds.length}
                          className="justify-start bg-sky-600 text-white hover:bg-sky-500"
                        >
                          <GitMerge className="mr-2 h-4 w-4" />
                          Merge selected candidates
                        </Button>
                        <Button
                          variant="secondary"
                          onClick={handleAssign}
                          disabled={pending || !employeeId}
                          className="justify-start bg-zinc-800 text-zinc-100 hover:bg-zinc-700"
                        >
                          <UserCheck className="mr-2 h-4 w-4" />
                          Assign to employee
                        </Button>
                        <Button
                          variant="ghost"
                          onClick={handleUndo}
                          disabled={pending || !latestReviewId}
                          className="justify-start text-zinc-400 hover:bg-zinc-900 hover:text-zinc-100"
                        >
                          <Link2 className="mr-2 h-4 w-4" />
                          Undo latest review
                        </Button>
                      </div>
                    </div>
                  </div>
                </CardContent>
              </Card>

              <Card className="border-zinc-800 bg-zinc-950 text-zinc-100">
                <CardHeader>
                  <CardTitle className="text-base font-semibold">Review History</CardTitle>
                </CardHeader>
                <CardContent>
                  <HistoryList items={historyItems} />
                </CardContent>
              </Card>
            </>
          ) : (
            <Card className="border-zinc-800 bg-zinc-950 text-zinc-100">
              <CardContent className="flex min-h-[320px] flex-col items-center justify-center px-6 py-12 text-center">
                <AlertTriangle className="h-10 w-10 text-zinc-600" />
                <p className="mt-4 text-base font-medium text-zinc-100">Select a review item</p>
                <p className="mt-2 max-w-md text-sm text-zinc-500">
                  The queue on the left lists live same-session candidates and cross-session cluster suggestions. Choose one to review the evidence and apply a merge or assignment.
                </p>
              </CardContent>
            </Card>
          )}
        </div>
      </div>
      {lightbox ? (
        <Lightbox
          src={lightbox.src}
          alt={lightbox.alt}
          onClose={() => setLightbox(null)}
        />
      ) : null}
    </div>
  );
}
