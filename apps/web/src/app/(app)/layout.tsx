import { AppShell } from "@/components/os/app-shell"

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return <AppShell>{children}</AppShell>
}
