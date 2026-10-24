"use client"

import { useQuery } from "@tanstack/react-query"
import { Briefcase, Building2, Download, KanbanSquare, LayoutDashboard, MessageSquare, Moon, Sun, User, Users, Video } from "lucide-react"
import { useRouter } from "next/navigation"
import { useTheme } from "next-themes"
import { useEffect, useState } from "react"

import {
  CommandDialog, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList, CommandSeparator,
} from "@/components/ui/command"
import { api } from "@/lib/api"

type Results = {
  opportunities: { id: number; title: string; company: string }[]
  applications: { id: number; code: string; title: string; company: string }[]
  contacts: { id: number; name: string; company: string }[]
  companies: string[]
  messages: { id: number; contact_id: number; excerpt: string }[]
}

export function CommandPalette({ open, onOpenChange }: { open: boolean; onOpenChange: (v: boolean) => void }) {
  const router = useRouter()
  const { resolvedTheme, setTheme } = useTheme()
  const [q, setQ] = useState("")
  const [debounced, setDebounced] = useState("")
  useEffect(() => {
    const t = setTimeout(() => setDebounced(q.trim()), 150)
    return () => clearTimeout(t)
  }, [q])
  const results = useQuery({ queryKey: ["search", debounced], queryFn: () => api.get<Results>(`/search?q=${encodeURIComponent(debounced)}`),
                             enabled: open && debounced.length >= 2 })

  function go(href: string) {
    onOpenChange(false)
    setQ("")
    router.push(href)
  }

  const r = results.data
  return (
    <CommandDialog open={open} onOpenChange={onOpenChange} title="Command palette" description="Search and run commands">
      <CommandInput placeholder="Type a command or search…" value={q} onValueChange={setQ} />
      <CommandList>
        <CommandEmpty>{debounced.length >= 2 && results.isFetching ? "Searching…" : "No results."}</CommandEmpty>
        {r && r.opportunities.length > 0 && (
          <CommandGroup heading="Opportunities">
            {r.opportunities.map((o) => (
              <CommandItem key={`o${o.id}`} value={`opp ${o.id} ${o.title} ${o.company}`} onSelect={() => go(`/opportunities/${o.id}`)}>
                <Briefcase /> <span className="truncate">{o.title}</span><span className="ml-auto text-xs text-muted-foreground">{o.company}</span>
              </CommandItem>
            ))}
          </CommandGroup>
        )}
        {r && r.applications.length > 0 && (
          <CommandGroup heading="Applications">
            {r.applications.map((a) => (
              <CommandItem key={`a${a.id}`} value={`app ${a.code} ${a.title} ${a.company}`} onSelect={() => go(`/applications/${a.id}`)}>
                <KanbanSquare /> {a.code} · <span className="truncate">{a.title}</span>
              </CommandItem>
            ))}
          </CommandGroup>
        )}
        {r && r.contacts.length > 0 && (
          <CommandGroup heading="Contacts">
            {r.contacts.map((c) => (
              <CommandItem key={`c${c.id}`} value={`contact ${c.name} ${c.company}`} onSelect={() => go(`/outreach/${c.id}`)}>
                <User /> {c.name}<span className="ml-auto text-xs text-muted-foreground">{c.company}</span>
              </CommandItem>
            ))}
          </CommandGroup>
        )}
        {r && r.companies.length > 0 && (
          <CommandGroup heading="Companies">
            {r.companies.map((c) => (
              <CommandItem key={`co${c}`} value={`company ${c}`} onSelect={() => go(`/opportunities?q=${encodeURIComponent(c)}`)}>
                <Building2 /> {c}
              </CommandItem>
            ))}
          </CommandGroup>
        )}
        {r && r.messages.length > 0 && (
          <CommandGroup heading="Messages">
            {r.messages.map((m) => (
              <CommandItem key={`m${m.id}`} value={`message ${m.id} ${m.excerpt}`} onSelect={() => go(`/outreach/${m.contact_id}`)}>
                <MessageSquare /> <span className="truncate">{m.excerpt}</span>
              </CommandItem>
            ))}
          </CommandGroup>
        )}
        <CommandSeparator />
        <CommandGroup heading="Commands">
          <CommandItem value="today priorities command center" onSelect={() => go("/")}><LayoutDashboard /> Show today&apos;s priorities</CommandItem>
          <CommandItem value="view interviews pipeline" onSelect={() => go("/pipeline?status=INTERVIEW")}><Video /> View interviews</CommandItem>
          <CommandItem value="generate message outreach networking" onSelect={() => go("/networking")}><MessageSquare /> Generate a message</CommandItem>
          <CommandItem value="contacts networking" onSelect={() => go("/networking?tab=contacts")}><Users /> Open contacts</CommandItem>
          <CommandItem value="export crm excel" onSelect={() => { onOpenChange(false); window.location.assign(new URL("/api/export/crm", window.location.origin).href) }}>
            <Download /> Export CRM (Excel)
          </CommandItem>
          <CommandItem value="switch theme dark light" onSelect={() => setTheme(resolvedTheme === "dark" ? "light" : "dark")}>
            {resolvedTheme === "dark" ? <Sun /> : <Moon />} Switch theme
          </CommandItem>
        </CommandGroup>
      </CommandList>
    </CommandDialog>
  )
}
