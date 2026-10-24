"use client"

import {
  type CollisionDetection, DndContext, type DragEndEvent, KeyboardSensor, PointerSensor, pointerWithin, rectIntersection, useDraggable, useDroppable,
  useSensor, useSensors,
} from "@dnd-kit/core"
import { CSS } from "@dnd-kit/utilities"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { CalendarClock, Columns3, Rows3 } from "lucide-react"
import Link from "next/link"
import { useRouter, useSearchParams } from "next/navigation"
import { Suspense, useState } from "react"
import { toast } from "sonner"

import { EmptyState, ErrorState, PageHeader, Pay, RowsSkeleton, Score, StatusBadge } from "@/components/os/bits"
import { ConfirmSubmission } from "@/components/os/dialogs"
import { Button } from "@/components/ui/button"
import { api } from "@/lib/api"
import { STATUS_LABEL, date } from "@/lib/format"
import type { ApplicationRow } from "@/lib/types"
import { cn } from "@/lib/utils"

type Board = { columns: { status: string; label: string }[]; items: ApplicationRow[] }
const BOARD = ["DISCOVERED", "SHORTLISTED", "TO_APPLY", "PREPARING", "APPLIED", "ONLINE_ASSESSMENT", "INTERVIEW", "FINAL_INTERVIEW",
               "OFFER_RECEIVED", "REJECTED"]
const COLUMN_OF: Record<string, string> = { OFFER_ACCEPTED: "OFFER_RECEIVED", WITHDRAWN: "REJECTED", CLOSED: "REJECTED" }
// Drop where the pointer is; fall back to overlap for keyboard dragging.
const collision: CollisionDetection = (args) => { const hits = pointerWithin(args); return hits.length ? hits : rectIntersection(args) }

function Card({ a }: { a: ApplicationRow }) {
  const { attributes, listeners, setNodeRef, transform, isDragging } = useDraggable({ id: a.id, data: a })
  return (
    <div ref={setNodeRef} style={{ transform: CSS.Translate.toString(transform) }} {...listeners} {...attributes}
         className={cn("cursor-grab touch-none rounded-lg border bg-card p-3 text-sm shadow-[0_1px_2px_oklch(0_0_0/0.04)] transition-[box-shadow,transform] hover:border-foreground/15 active:cursor-grabbing",
                       isDragging && "z-30 rotate-[0.6deg] shadow-lg ring-1 ring-brand/40")}>
      <Link href={`/applications/${a.id}`} className="block" onClick={(e) => isDragging && e.preventDefault()}>
        <div className="flex items-center justify-between gap-2">
          <span className="truncate text-xs font-medium text-muted-foreground">{a.company}</span>
          <span className="text-[10px] text-muted-foreground">{a.code}</span>
        </div>
        <div className="mt-0.5 line-clamp-2 font-medium leading-snug">{a.title}</div>
        <div className="mt-2 flex items-center justify-between gap-2">
          <Pay c={a.compensation} compact />
          <Score value={a.priority} />
        </div>
        <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-muted-foreground">
          {a.country && <span>{a.country}</span>}
          {a.deadline && <span className="inline-flex items-center gap-1"><CalendarClock className="size-3" />{date(a.deadline)}</span>}
          {a.next_action && <span className="truncate">→ {a.next_action}</span>}
        </div>
      </Link>
    </div>
  )
}

function Column({ status, items }: { status: string; items: ApplicationRow[] }) {
  const { setNodeRef, isOver } = useDroppable({ id: status })
  return (
    <div ref={setNodeRef} data-status={status} className={cn("flex shrink-0 flex-col rounded-xl bg-muted/50 transition-[width,background-color]",
                                                           items.length ? "w-64" : "w-44", isOver && "w-64 bg-brand-soft ring-1 ring-brand/40")}>
      <div className="flex items-center justify-between px-3 pb-1.5 pt-2.5 text-xs font-medium">
        <StatusBadge status={status} /><span className="tabular text-muted-foreground">{items.length}</span>
      </div>
      <div className="flex min-h-24 flex-col gap-2 px-2 pb-2">
        {items.map((a) => <Card key={a.id} a={a} />)}
        {!items.length && <div className="flex flex-1 items-center justify-center rounded-lg border border-dashed text-[11px] text-muted-foreground">Dépose ici</div>}
      </div>
    </div>
  )
}

function PipelineView() {
  const qc = useQueryClient()
  const router = useRouter()
  const params = useSearchParams()
  const view = params.get("view") ?? "board"
  const only = params.get("status")
  const q = useQuery({ queryKey: ["pipeline"], queryFn: () => api.get<Board>("/pipeline") })
  const [pending, setPending] = useState<{ app: ApplicationRow; status: string } | null>(null)
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 6 } }), useSensor(KeyboardSensor))
  const move = useMutation({
    mutationFn: ({ id, status, appliedOn }: { id: number; status: string; appliedOn?: string }) =>
      api.post<ApplicationRow>(`/applications/${id}/status`, { status, confirm_submission: !!appliedOn, applied_on: appliedOn }),
    onMutate: async ({ id, status }) => {
      await qc.cancelQueries({ queryKey: ["pipeline"] })
      const prev = qc.getQueryData<Board>(["pipeline"])
      qc.setQueryData<Board>(["pipeline"], (b) => b && { ...b, items: b.items.map((i) => (i.id === id ? { ...i, status } : i)) })
      return { prev }
    },
    onError: (e, _v, ctx) => { if (ctx?.prev) qc.setQueryData(["pipeline"], ctx.prev); toast.error((e as Error).message) },
    onSuccess: (a) => toast.success(`${a.company} : ${STATUS_LABEL[a.status]}`),
    onSettled: () => qc.invalidateQueries(),
  })

  function onDragEnd(e: DragEndEvent) {
    const app = e.active.data.current as ApplicationRow | undefined
    const target = e.over?.id as string | undefined
    if (!app || !target || target === (COLUMN_OF[app.status] ?? app.status)) return
    const needsConfirm = !app.date_applied && ["APPLIED", "ONLINE_ASSESSMENT", "INTERVIEW", "FINAL_INTERVIEW", "OFFER_RECEIVED"].includes(target)
    if (needsConfirm) setPending({ app, status: target })
    else move.mutate({ id: app.id, status: target })
  }

  const items = (q.data?.items ?? []).filter((a) => !only || a.status === only)
  return (
    <div>
      <PageHeader title="Candidatures" description={only ? `Filtré sur « ${STATUS_LABEL[only] ?? only} »` : "Glisse une carte pour changer son statut. Un envoi demande toujours ta confirmation."}
                  actions={<div className="flex rounded-lg border bg-card p-0.5">
                    <Button size="xs" variant={view === "board" ? "secondary" : "ghost"} onClick={() => router.replace(`/pipeline${only ? `?status=${only}` : ""}`)}><Columns3 /> Tableau</Button>
                    <Button size="xs" variant={view === "table" ? "secondary" : "ghost"} onClick={() => router.replace(`/pipeline?view=table${only ? `&status=${only}` : ""}`)}><Rows3 /> Liste</Button>
                  </div>} />
      <ConfirmSubmission open={!!pending} onOpenChange={(v) => !v && setPending(null)} title={pending ? `${pending.app.company}, ${pending.app.title}` : ""}
                         onConfirm={(day) => { if (pending) move.mutate({ id: pending.app.id, status: pending.status, appliedOn: day }); setPending(null) }} />
      {q.isError ? <ErrorState error={q.error} retry={() => q.refetch()} /> : q.isLoading ? <RowsSkeleton rows={8} /> : !q.data?.items.length ? (
        <EmptyState title="Aucune candidature suivie" description="Suis une offre pour la retrouver ici."
                    action={<Button size="sm" variant="outline" render={<Link href="/opportunities" />}>Voir les opportunités</Button>} />
      ) : view === "table" ? (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-xs text-muted-foreground"><tr className="border-b">
              {["Réf.", "Entreprise", "Poste", "Statut", "Priorité", "Paie", "Échéance", "Envoyée", "Prochaine action"].map((h) => <th key={h} className="whitespace-nowrap px-3 py-2.5 text-left font-medium first:pl-6 lg:first:pl-8">{h}</th>)}
            </tr></thead>
            <tbody className="divide-y">
              {items.map((a) => (
                <tr key={a.id} className="cursor-pointer transition-colors hover:bg-subtle" onClick={() => router.push(`/applications/${a.id}`)}>
                  <td className="px-3 py-2 pl-6 text-muted-foreground lg:pl-8">{a.code}</td>
                  <td className="px-3 py-2 font-medium">{a.company}</td>
                  <td className="max-w-80 truncate px-3 py-2">{a.title}</td>
                  <td className="px-3 py-2"><StatusBadge status={a.status} /></td>
                  <td className="px-3 py-2"><Score value={a.priority} /></td>
                  <td className="px-3 py-2"><Pay c={a.compensation} compact /></td>
                  <td className="tabular px-3 py-2 text-muted-foreground">{date(a.deadline)}</td>
                  <td className="tabular px-3 py-2 text-muted-foreground">{date(a.date_applied)}</td>
                  <td className="max-w-60 truncate px-3 py-2 text-muted-foreground">{a.next_action ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <DndContext sensors={sensors} collisionDetection={collision} onDragEnd={onDragEnd}>
          <div className="flex items-start gap-3 overflow-x-auto px-6 pb-8 pt-4 lg:px-8">
            {BOARD.map((status) => <Column key={status} status={status} items={items.filter((a) => (COLUMN_OF[a.status] ?? a.status) === status)} />)}
          </div>
        </DndContext>
      )}
    </div>
  )
}

export default function PipelinePage() {
  return <Suspense fallback={<RowsSkeleton rows={8} />}><PipelineView /></Suspense>
}
