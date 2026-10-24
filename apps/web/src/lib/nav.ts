/** Only a path on this origin: "//evil", "/\\evil" or "https://evil" resolve elsewhere and fall back to "/". */
export function safeNext(next: string | null): string {
  if (!next) return "/"
  try {
    const url = new URL(next, window.location.origin)
    return url.origin === window.location.origin ? `${url.pathname}${url.search}${url.hash}` : "/"
  } catch {
    return "/"
  }
}
