"use client"

import { useQuery } from "@tanstack/react-query"
import Link from "next/link"

import { EmptyState, ErrorState, PageHeader, RowsSkeleton } from "@/components/os/bits"
import { api } from "@/lib/api"
import { EVENT_LABEL, SOURCE_LABEL, STATUS_LABEL, date } from "@/lib/format"

type Event = { at: string; type: string; company: string | null; new: string | null; source: string; application_id: number | null }

export default function ActivityPage() {
  const q = useQuery({ queryKey: ["activity"], queryFn: () => api.get<Event[]>("/activity") })
  return (
    <div>
      <PageHeader title="Journal" description="Chaque changement du CRM, depuis l’app web, Telegram, Excel ou le terminal. Rien n’est effacé." />
      {q.isError ? <ErrorState error={q.error} retry={() => q.refetch()} /> : q.isLoading ? <RowsSkeleton rows={12} /> : !q.data?.length ? <EmptyState title="Aucune activité pour l’instant" /> : (
        <ul className="divide-y">
          {q.data.map((e, i) => (
            <li key={i} className="flex items-center gap-4 px-6 py-2.5 text-sm transition-colors hover:bg-subtle lg:px-8">
              <span className="tabular w-36 shrink-0 text-xs text-muted-foreground">{date(e.at, true)} · {new Date(e.at).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" })}</span>
              <span className="w-20 shrink-0 text-xs text-muted-foreground">{SOURCE_LABEL[e.source] ?? e.source}</span>
              <span className="font-medium">{EVENT_LABEL[e.type] ?? e.type.replaceAll("_", " ")}</span>
              <span className="truncate text-muted-foreground">{e.company}{e.new ? ` · ${STATUS_LABEL[e.new] ?? e.new}` : ""}</span>
              {e.application_id && <Link href={`/applications/${e.application_id}`} className="ml-auto text-xs text-brand hover:underline">Ouvrir</Link>}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
