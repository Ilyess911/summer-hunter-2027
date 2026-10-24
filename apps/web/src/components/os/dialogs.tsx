"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Loader2 } from "lucide-react"
import { useState, type ReactNode } from "react"
import { useForm } from "react-hook-form"
import { toast } from "sonner"
import { z } from "zod"

import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter,
  AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { api } from "@/lib/api"
import type { ContactRow } from "@/lib/types"

export function ConfirmDialog({ open, onOpenChange, title, description, confirm, onConfirm, children, destructive }: {
  open: boolean; onOpenChange: (v: boolean) => void; title: string; description: ReactNode; confirm: string
  onConfirm: () => void; children?: ReactNode; destructive?: boolean
}) {
  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{title}</AlertDialogTitle>
          <AlertDialogDescription>{description}</AlertDialogDescription>
        </AlertDialogHeader>
        {children}
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction variant={destructive ? "destructive" : "default"} onClick={() => { onConfirm(); onOpenChange(false) }}>{confirm}</AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

/** Confirmation required before an application is recorded as submitted (it is never inferred). */
export function ConfirmSubmission({ open, onOpenChange, onConfirm, title }: {
  open: boolean; onOpenChange: (v: boolean) => void; onConfirm: (appliedOn: string) => void; title: string
}) {
  const today = new Date().toISOString().slice(0, 10)
  const [day, setDay] = useState(today)
  return (
    <ConfirmDialog open={open} onOpenChange={onOpenChange} title="Mark as submitted?" confirm="Yes, it was submitted"
                   description={<>Confirm that you actually submitted your application for <b>{title}</b>. Your CV and cover letter will be frozen as the submitted version.</>}
                   onConfirm={() => onConfirm(day)}>
      <div className="space-y-1.5">
        <Label htmlFor="applied-on">Submission date</Label>
        <Input id="applied-on" type="date" max={today} value={day} onChange={(e) => setDay(e.target.value)} />
      </div>
    </ConfirmDialog>
  )
}

export function PromptDialog({ open, onOpenChange, title, label, onSubmit }: {
  open: boolean; onOpenChange: (v: boolean) => void; title: string; label: string; onSubmit: (value: string) => void
}) {
  const [value, setValue] = useState("")
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-sm">
        <form onSubmit={(e) => { e.preventDefault(); if (value.trim()) { onSubmit(value.trim()); setValue(""); onOpenChange(false) } }} className="space-y-4">
          <DialogHeader><DialogTitle>{title}</DialogTitle></DialogHeader>
          <div className="space-y-1.5"><Label htmlFor="prompt-value">{label}</Label>
            <Input id="prompt-value" autoFocus value={value} onChange={(e) => setValue(e.target.value)} maxLength={60} /></div>
          <DialogFooter><Button type="submit" disabled={!value.trim()}>Save</Button></DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

const CATEGORIES: [string, string][] = [
  ["hiring_manager", "Hiring manager"], ["team_manager", "Team manager"], ["talent_acquisition", "Talent acquisition"],
  ["university_recruiter", "University recruiter"], ["program_coordinator", "Internship programme coordinator"],
  ["team_member", "Team member"], ["referral", "Referral contact"], ["alumni_esilv", "ESILV alumni"],
  ["alumni_af_airbus", "Former Air France / Airbus"], ["other", "Other"],
]
const contactSchema = z.object({
  name: z.string().trim().min(2, "Name required").max(160),
  position: z.string().trim().max(200).optional(),
  linkedin_url: z.union([z.literal(""), z.string().trim().regex(/^https:\/\/([a-z]{2,3}\.)?linkedin\.com\/in\/[A-Za-z0-9\-_%]+\/?$/, "Use the public profile URL, https://www.linkedin.com/in/…")]),
  category: z.string(),
  note: z.string().trim().max(600).optional(),
})

export function AddContactDialog({ open, onOpenChange, company, opportunityId }: {
  open: boolean; onOpenChange: (v: boolean) => void; company: string; opportunityId?: number
}) {
  const qc = useQueryClient()
  const form = useForm<z.infer<typeof contactSchema>>({ resolver: zodResolver(contactSchema),
    defaultValues: { name: "", position: "", linkedin_url: "", category: "talent_acquisition", note: "" } })
  const add = useMutation({
    mutationFn: (v: z.infer<typeof contactSchema>) => api.post<ContactRow>("/contacts", { ...v, company, opportunity_id: opportunityId,
                                                                                           linkedin_url: v.linkedin_url || null }),
    onSuccess: (c) => {
      toast.success(`${c.name} added`)
      qc.invalidateQueries()
      form.reset()
      onOpenChange(false)
    },
    onError: (e) => toast.error((e as Error).message),
  })
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <form onSubmit={form.handleSubmit((v) => add.mutate(v))} className="space-y-4">
          <DialogHeader>
            <DialogTitle>Add a contact at {company}</DialogTitle>
            <DialogDescription>Only people you found yourself or with public evidence. No guessed emails.</DialogDescription>
          </DialogHeader>
          <div className="grid gap-3">
            <div className="space-y-1.5"><Label htmlFor="c-name">Name</Label><Input id="c-name" {...form.register("name")} aria-invalid={!!form.formState.errors.name} />
              {form.formState.errors.name && <p className="text-xs text-destructive">{form.formState.errors.name.message}</p>}</div>
            <div className="space-y-1.5"><Label htmlFor="c-pos">Position</Label><Input id="c-pos" placeholder="University Recruiter" {...form.register("position")} /></div>
            <div className="space-y-1.5"><Label htmlFor="c-url">LinkedIn profile</Label>
              <Input id="c-url" placeholder="https://www.linkedin.com/in/…" {...form.register("linkedin_url")} aria-invalid={!!form.formState.errors.linkedin_url} />
              {form.formState.errors.linkedin_url && <p className="text-xs text-destructive">{form.formState.errors.linkedin_url.message}</p>}</div>
            <div className="space-y-1.5"><Label htmlFor="c-cat">Relationship to the opening</Label>
              <select id="c-cat" {...form.register("category")} className="h-8 w-full rounded-md border bg-background px-2 text-sm">
                {CATEGORIES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select></div>
            <div className="space-y-1.5"><Label htmlFor="c-note">Why this person (evidence)</Label>
              <Input id="c-note" placeholder="Posted about the internship on 20 Oct" {...form.register("note")} /></div>
          </div>
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button type="submit" disabled={add.isPending}>{add.isPending && <Loader2 className="animate-spin" />} Add contact</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
