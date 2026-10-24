"use client"

import { useQuery } from "@tanstack/react-query"
import {
  Activity, BarChart3, Bell, Briefcase, Crosshair, Gauge, KanbanSquare, LayoutDashboard, LogOut, MessagesSquare, Monitor,
  Moon, Search, Sun, Users,
} from "lucide-react"
import Link from "next/link"
import { usePathname, useRouter } from "next/navigation"
import { useTheme } from "next-themes"
import { useEffect, useState } from "react"

import { CommandPalette } from "@/components/os/command-palette"
import { Kbd } from "@/components/os/bits"
import { Button } from "@/components/ui/button"
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import {
  Sidebar, SidebarContent, SidebarFooter, SidebarGroup, SidebarGroupContent, SidebarGroupLabel, SidebarHeader, SidebarInset,
  SidebarMenu, SidebarMenuBadge, SidebarMenuButton, SidebarMenuItem, SidebarProvider, SidebarTrigger,
} from "@/components/ui/sidebar"
import { api } from "@/lib/api"
import { relative } from "@/lib/format"
import type { CommandCenter, Meta } from "@/lib/types"

const NAV = [
  { href: "/", label: "Command Center", icon: LayoutDashboard },
  { href: "/opportunities", label: "Opportunities", icon: Briefcase },
  { href: "/pipeline", label: "Pipeline", icon: KanbanSquare },
  { href: "/networking", label: "Networking", icon: Users },
  { href: "/outreach", label: "Outreach Studio", icon: MessagesSquare },
]
const INSIGHTS = [
  { href: "/analytics", label: "Analytics", icon: BarChart3 },
  { href: "/performance", label: "Hunter Performance", icon: Gauge },
  { href: "/activity", label: "Activity", icon: Activity },
]

function active(path: string, href: string) {
  return href === "/" ? path === "/" : path === href || path.startsWith(`${href}/`)
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const path = usePathname()
  const router = useRouter()
  const [palette, setPalette] = useState(false)
  const meta = useQuery({ queryKey: ["meta"], queryFn: () => api.get<Meta>("/meta") })
  const cc = useQuery({ queryKey: ["command-center"], queryFn: () => api.get<CommandCenter>("/command-center") })
  const { setTheme } = useTheme()

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault()
        setPalette((v) => !v)
      }
      const target = e.target as HTMLElement
      if (target?.closest("input, textarea, [contenteditable]") || e.metaKey || e.ctrlKey || e.altKey) return
      const go: Record<string, string> = { "1": "/", "2": "/opportunities", "3": "/pipeline", "4": "/networking", "5": "/outreach" }
      if (go[e.key]) router.push(go[e.key])
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [router])

  async function logout() {
    await api.post("/auth/logout")
    router.replace("/login")
  }

  const pending = cc.data?.priorities.length ?? 0
  return (
    <SidebarProvider>
      <Sidebar collapsible="icon" variant="sidebar">
        <SidebarHeader>
          <SidebarMenu>
            <SidebarMenuItem>
              <SidebarMenuButton size="lg" render={<Link href="/" />}>
                <span className="flex size-7 items-center justify-center rounded-md bg-primary text-primary-foreground">
                  <Crosshair className="size-4" />
                </span>
                <span className="flex flex-col leading-tight">
                  <span className="text-[13px] font-semibold">Summer Hunter OS</span>
                  <span className="text-[11px] text-muted-foreground">Internships Apr–Aug 2027</span>
                </span>
              </SidebarMenuButton>
            </SidebarMenuItem>
          </SidebarMenu>
        </SidebarHeader>
        <SidebarContent>
          <SidebarGroup>
            <SidebarGroupLabel>Workspace</SidebarGroupLabel>
            <SidebarGroupContent>
              <SidebarMenu>
                {NAV.map((n, i) => (
                  <SidebarMenuItem key={n.href}>
                    <SidebarMenuButton isActive={active(path, n.href)} tooltip={`${n.label} (${i + 1})`} render={<Link href={n.href} />}>
                      <n.icon /> <span>{n.label}</span>
                    </SidebarMenuButton>
                    {n.href === "/" && pending > 0 && <SidebarMenuBadge>{pending}</SidebarMenuBadge>}
                  </SidebarMenuItem>
                ))}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
          <SidebarGroup>
            <SidebarGroupLabel>Insights</SidebarGroupLabel>
            <SidebarGroupContent>
              <SidebarMenu>
                {INSIGHTS.map((n) => (
                  <SidebarMenuItem key={n.href}>
                    <SidebarMenuButton isActive={active(path, n.href)} tooltip={n.label} render={<Link href={n.href} />}>
                      <n.icon /> <span>{n.label}</span>
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                ))}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
        </SidebarContent>
        <SidebarFooter>
          <div className="px-2 pb-1 text-[11px] leading-5 text-muted-foreground group-data-[collapsible=icon]:hidden">
            <div className="flex items-center gap-1.5">
              <span className={`size-1.5 rounded-full ${meta.data?.mode === "hosted" ? "bg-info" : "bg-success"}`} />
              {meta.data?.mode === "hosted" ? `Hosted · synced ${relative(meta.data.last_sync)}` : "Local · live database"}
            </div>
            <div>Last discovery run {relative(meta.data?.last_run?.finished ?? meta.data?.last_run?.started)}</div>
            <div>Mode: {meta.data?.recruitment_mode ?? "…"}</div>
          </div>
        </SidebarFooter>
      </Sidebar>
      <SidebarInset className="min-w-0">
        <header className="sticky top-0 z-20 flex h-12 items-center gap-2 border-b bg-background/85 px-3 backdrop-blur">
          <SidebarTrigger />
          <button onClick={() => setPalette(true)}
                  className="flex h-8 w-full max-w-md items-center gap-2 rounded-md border bg-subtle px-2.5 text-sm text-muted-foreground transition-colors hover:bg-muted">
            <Search className="size-4" /> Search companies, roles, contacts…
            <span className="ml-auto"><Kbd>⌘K</Kbd></span>
          </button>
          <div className="ml-auto flex items-center gap-1">
            <Popover>
              <PopoverTrigger render={<Button variant="ghost" size="icon" aria-label="Action center" className="relative" />}>
                <Bell />
                {pending > 0 && <span className="absolute right-1.5 top-1.5 size-1.5 rounded-full bg-primary" />}
              </PopoverTrigger>
              <PopoverContent align="end" className="w-96 p-0">
                <div className="border-b px-3 py-2 text-[13px] font-medium">Action center</div>
                <div className="max-h-96 overflow-y-auto py-1">
                  {(cc.data?.priorities ?? []).slice(0, 12).map((p) => (
                    <Link key={p.key} href={p.href} className="block px-3 py-2 text-sm hover:bg-muted">
                      <div className="font-medium leading-snug">{p.title}</div>
                      <div className="truncate text-xs text-muted-foreground">{p.detail}</div>
                    </Link>
                  ))}
                  {pending === 0 && <p className="px-3 py-6 text-center text-sm text-muted-foreground">Nothing needs you right now.</p>}
                </div>
              </PopoverContent>
            </Popover>
            <DropdownMenu>
              <DropdownMenuTrigger render={<Button variant="ghost" size="icon" aria-label="Theme" />}>
                <Sun className="dark:hidden" /><Moon className="hidden dark:block" />
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem onClick={() => setTheme("light")}><Sun /> Light</DropdownMenuItem>
                <DropdownMenuItem onClick={() => setTheme("dark")}><Moon /> Dark</DropdownMenuItem>
                <DropdownMenuItem onClick={() => setTheme("system")}><Monitor /> System</DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
            <Button variant="ghost" size="icon" aria-label="Sign out" onClick={logout}><LogOut /></Button>
          </div>
        </header>
        <main className="min-w-0 flex-1">{children}</main>
      </SidebarInset>
      <CommandPalette open={palette} onOpenChange={setPalette} />
    </SidebarProvider>
  )
}
