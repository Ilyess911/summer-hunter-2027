"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { Loader2 } from "lucide-react"
import { useRouter, useSearchParams } from "next/navigation"
import { Suspense, useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"

import { BrandMark } from "@/components/os/bits"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { api, ApiError } from "@/lib/api"
import { safeNext } from "@/lib/nav"

const schema = z.object({ password: z.string().min(1, "Saisis ton mot de passe") })

function LoginForm() {
  const router = useRouter()
  const params = useSearchParams()
  const [error, setError] = useState<string | null>(null)
  const form = useForm<z.infer<typeof schema>>({ resolver: zodResolver(schema), defaultValues: { password: "" } })

  async function onSubmit(values: z.infer<typeof schema>) {
    setError(null)
    try {
      await api.post("/auth/login", values)
      router.replace(safeNext(params.get("next")))
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Impossible de joindre l’API")
    }
  }

  return (
    <form onSubmit={form.handleSubmit(onSubmit)} className="w-full max-w-sm space-y-6">
      <div className="space-y-2">
        <BrandMark className="size-10 rounded-xl [&_svg]:size-5" />
        <h1 className="pt-2 text-[22px] font-semibold">Summer Hunter</h1>
        <p className="text-sm text-muted-foreground">Espace privé. Connecte-toi pour continuer.</p>
      </div>
      <div className="space-y-2">
        <Label htmlFor="password">Mot de passe</Label>
        <Input id="password" type="password" autoComplete="current-password" autoFocus {...form.register("password")}
               aria-invalid={!!form.formState.errors.password || !!error} />
        {(form.formState.errors.password || error) && (
          <p className="text-sm text-destructive" role="alert">{form.formState.errors.password?.message ?? error}</p>
        )}
      </div>
      <Button type="submit" className="w-full" size="lg" disabled={form.formState.isSubmitting}>
        {form.formState.isSubmitting && <Loader2 className="animate-spin" />} Se connecter
      </Button>
    </form>
  )
}

export default function LoginPage() {
  return (
    <main className="flex min-h-svh items-center justify-center px-4">
      <Suspense>
        <LoginForm />
      </Suspense>
    </main>
  )
}
