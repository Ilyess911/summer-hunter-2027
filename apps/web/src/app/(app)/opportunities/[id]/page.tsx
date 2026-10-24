"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { ArrowLeft, ExternalLink, KanbanSquare, Plus, ThumbsDown } from "lucide-react"
import Link from "next/link"
import { useParams, useRouter } from "next/navigation"
import { useState } from "react"
import { toast } from "sonner"

import { ErrorState, PageHeader, RowsSkeleton, StatusBadge } from "@/components/os/bits"
import { ConfirmDialog } from "@/components/os/dialogs"
import { WorkspaceGrid } from "@/components/os/opportunity-workspace"
import { Button } from "@/components/ui/button"
import { api } from "@/lib/api"
import { career } from "@/lib/format"
import type { OpportunityDetail } from "@/lib/types"

export default function OpportunityPage() {
  const { id } = useParams<{ id: string }>()
  const router = useRouter()
  const qc = useQueryClient()
  const [dismissing, setDismissing] = useState(false)
  const q = useQuery({ queryKey: ["opportunity", Number(id)], queryFn: () => api.get<OpportunityDetail>(`/opportunities/${id}`) })
  const track = useMutation({
    mutationFn: () => api.post<{ application_id: number; created: boolean }>(`/opportunities/${id}/track`),
    onSuccess: (r) => { toast.success(r.created ? "Ajoutée à tes candidatures" : "Déjà suivie"); qc.invalidateQueries(); router.push(`/applications/${r.application_id}`) },
    onError: (e) => toast.error((e as Error).message),
  })
  const dismiss = useMutation({
    mutationFn: () => api.post(`/opportunities/${id}/dismiss`, { reason: "role" }),
    onSuccess: () => { toast.success("Écartée : elle quitte tes listes"); qc.invalidateQueries(); router.push("/opportunities") },
  })
  if (q.isError) return <ErrorState error={q.error} retry={() => q.refetch()} />
  const o = q.data
  return (
    <div>
      <div className="px-6 pt-4 lg:px-8"><Button variant="ghost" size="xs" className="-ml-2 text-muted-foreground" onClick={() => router.back()}><ArrowLeft /> Retour</Button></div>
      {!o ? <RowsSkeleton rows={10} /> : (
        <>
          <PageHeader title={o.title}
                      description={<>{o.company} · {[o.city, o.country].filter(Boolean).join(", ") || o.location} · {career(o.category)}</>}
                      actions={<>
                        {o.application ? (
                          <Button size="sm" variant="outline" render={<Link href={`/applications/${o.application.id}`} />}>
                            <KanbanSquare /> {o.application.code} · <StatusBadge status={o.application.status} />
                          </Button>
                        ) : <Button size="sm" onClick={() => track.mutate()} disabled={track.isPending}><Plus /> Suivre la candidature</Button>}
                        {o.apply_url && <Button size="sm" variant="outline" render={<a href={o.apply_url} target="_blank" rel="noopener noreferrer" />}><ExternalLink /> Postuler sur le site</Button>}
                        {!o.application && <Button size="sm" variant="ghost" onClick={() => setDismissing(true)}><ThumbsDown /> Écarter</Button>}
                      </>} />
          <ConfirmDialog open={dismissing} onOpenChange={setDismissing} title="Écarter cette offre ?" destructive confirm="Écarter"
                         description="Elle quitte tes listes, et le classement apprend que ce type de poste t’intéresse moins."
                         onConfirm={() => dismiss.mutate()} />
          <WorkspaceGrid o={o} />
        </>
      )}
    </div>
  )
}
