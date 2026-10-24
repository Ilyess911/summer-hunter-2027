"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { BadgeCheck, ExternalLink, PenLine, RefreshCw, Search } from "lucide-react"
import Link from "next/link"
import { useRouter, useSearchParams } from "next/navigation"
import { Suspense, useState } from "react"
import { toast } from "sonner"

import { EmptyState, ErrorState, PageHeader, RowsSkeleton, Score, TimingBadge } from "@/components/os/bits"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { api, qs } from "@/lib/api"
import { date } from "@/lib/format"
import type { ContactRow } from "@/lib/types"
import { cn } from "@/lib/utils"

function ContactLine({ c }: { c: ContactRow }) {
  return (
    <li className="grid grid-cols-[1fr_auto] items-center gap-4 px-6 py-3 transition-colors hover:bg-subtle md:grid-cols-[minmax(0,2fr)_minmax(0,2fr)_auto_auto] lg:px-8">
      <div className="min-w-0">
        <div className="flex items-center gap-1.5 text-sm font-medium">
          {c.name}
          {c.verification === "verified" ? <BadgeCheck className="size-3.5 text-success" aria-label="Vérifié" />
            : <span className="rounded bg-warning/12 px-1 text-[10px] font-medium text-warning">Non vérifié</span>}
          {c.due && <span className="rounded bg-brand-soft px-1.5 text-[10px] font-medium text-brand">À faire</span>}
        </div>
        <div className="truncate text-xs text-muted-foreground">{c.category_label}{c.position ? ` · ${c.position}` : ""} · {c.company}</div>
      </div>
      <div className="hidden min-w-0 md:block">
        <div className="truncate text-sm">{c.opportunity?.title ?? "Aucune offre liée"}</div>
        <div className="flex items-center gap-2 truncate text-xs text-muted-foreground">
          <TimingBadge action={c.timing?.action} />{c.timing?.date && <span>· {date(c.timing.date)}</span>}<span className="truncate">· {c.timing?.reason}</span>
        </div>
      </div>
      <Score value={c.score} label="Priorité du contact" />
      <div className="flex gap-1">
        {c.linkedin_url && <Button size="icon-sm" variant="ghost" aria-label="Ouvrir LinkedIn" render={<a href={c.linkedin_url} target="_blank" rel="noopener noreferrer" />}><ExternalLink /></Button>}
        <Button size="sm" variant="outline" render={<Link href={`/outreach/${c.id}${c.opportunity ? `?opportunity=${c.opportunity.id}` : ""}`} />}><PenLine /> Écrire</Button>
      </div>
    </li>
  )
}

function NetworkingView() {
  const params = useSearchParams()
  const router = useRouter()
  const qc = useQueryClient()
  const tab = params.get("tab") ?? "queue"
  const [q, setQ] = useState("")
  const queue = useQuery({ queryKey: ["networking"], queryFn: () => api.get<ContactRow[]>("/networking"), enabled: tab === "queue" })
  const contacts = useQuery({ queryKey: ["contacts", q], queryFn: () => api.get<ContactRow[]>(`/contacts${qs({ q })}`), enabled: tab === "contacts" })
  const refresh = useMutation({
    mutationFn: () => api.post<{ created: number; scored: number }>("/networking/refresh"),
    onSuccess: (r) => { toast.success(`${r.created} contact(s) importé(s) depuis des preuves publiques, ${r.scored} classé(s)`); qc.invalidateQueries() },
    onError: (e) => toast.error((e as Error).message),
  })
  const data = tab === "queue" ? queue : contacts
  return (
    <div>
      <PageHeader title="Réseau"
                  description="Qui contacter, pourquoi et quand. Les contacts viennent de preuves publiques ou de toi ; rien n’est envoyé automatiquement."
                  actions={<Button size="sm" variant="outline" onClick={() => refresh.mutate()} disabled={refresh.isPending}><RefreshCw className={cn(refresh.isPending && "animate-spin")} /> Actualiser</Button>} />
      <div className="flex items-center gap-2 border-b px-6 pb-3 pt-2 lg:px-8">
        {[["queue", "À contacter"], ["contacts", "Tous les contacts"]].map(([id, label]) => (
          <Button key={id} size="sm" variant={tab === id ? "secondary" : "ghost"} onClick={() => router.replace(`/networking?tab=${id}`)}>{label}</Button>
        ))}
        {tab === "contacts" && (
          <div className="relative ml-auto">
            <Search className="pointer-events-none absolute left-2 top-2 size-4 text-muted-foreground" />
            <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Nom, entreprise, fonction" className="h-8 w-60 rounded-lg pl-8" />
          </div>
        )}
      </div>
      {data.isError ? <ErrorState error={data.error} retry={() => data.refetch()} /> : data.isLoading ? <RowsSkeleton rows={8} /> : !data.data?.length ? (
        <EmptyState title={tab === "queue" ? "Personne à contacter pour l’instant" : "Aucun contact pour l’instant"}
                    description="Ouvre une offre, lance les recherches LinkedIn proposées et ajoute le recruteur ou le manager trouvé. Les posts publics qui nomment un recruteur sont importés automatiquement."
                    action={<Button size="sm" variant="outline" render={<Link href="/opportunities?view=high-recruitability" />}>Voir les meilleures offres</Button>} />
      ) : (
        <ul className="divide-y">{data.data.map((c) => <ContactLine key={`${c.id}-${c.opportunity?.id ?? 0}`} c={c} />)}</ul>
      )}
    </div>
  )
}

export default function NetworkingPage() {
  return <Suspense fallback={<RowsSkeleton rows={8} />}><NetworkingView /></Suspense>
}
