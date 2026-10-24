"use client"

import { useQuery } from "@tanstack/react-query"
import Link from "next/link"

import { EmptyState, ErrorState, PageHeader, RowsSkeleton } from "@/components/os/bits"
import { api } from "@/lib/api"
import { date } from "@/lib/format"

type Event = { at: string; type: string; company: string | null; new: string | null; source: string; application_id: number | null }

export default function ActivityPage() {
  const q = useQuery({ queryKey: ["activity"], queryFn: () => api.get<Event[]>("/activity") })
  return (
    <div>
      <PageHeader title="Activity" description="Append-only log of every CRM change, from the web app, Telegram, Excel or the CLI." />
      {q.isError ? <ErrorState error={q.error} retry={() => q.refetch()} /> : q.isLoading ? <RowsSkeleton rows={12} /> : !q.data?.length ? <EmptyState title="No activity yet" /> : (
        <ul className="divide-y">
          {q.data.map((e, i) => (
            <li key={i} className="flex items-center gap-4 px-6 py-2 text-sm">
              <span className="tabular w-40 shrink-0 text-xs text-muted-foreground">{date(e.at, true)} {new Date(e.at).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })}</span>
              <span className="w-20 shrink-0 text-xs text-muted-foreground">{e.source}</span>
              <span className="font-medium">{e.type.replaceAll("_", " ")}</span>
              <span className="truncate text-muted-foreground">{e.company}{e.new ? ` · ${e.new}` : ""}</span>
              {e.application_id && <Link href={`/applications/${e.application_id}`} className="ml-auto text-xs text-primary hover:underline">Open</Link>}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
