import { describe, expect, it } from "vitest"

import { qs } from "@/lib/api"
import { pay, relative } from "@/lib/format"
import { safeNext } from "@/lib/nav"

describe("pay", () => {
  it("shows a monthly range when min and max differ", () => {
    expect(pay({ status: "PAID_CONFIRMED", monthly_min_eur: 5420, monthly_max_eur: 6233 })).toBe("5\u202f420–6\u202f233 €/mois")
  })
  it("collapses a range that is effectively one value", () => {
    expect(pay({ status: "PAID_CONFIRMED", monthly_min_eur: 2000, monthly_max_eur: 2005 })).toBe("2\u202f000 €/mois")
  })
  it("never turns an unknown amount into zero or unpaid", () => {
    expect(pay({ status: null, monthly_min_eur: null })).toBe("Non précisé")
    expect(pay({ status: "PAID_AMOUNT_UNKNOWN", monthly_min_eur: null })).toBe("Payé, montant ?")
  })
  it("says unpaid only when confirmed", () => {
    expect(pay({ status: "UNPAID_CONFIRMED", monthly_min_eur: null })).toBe("Non payé")
  })
})

describe("relative", () => {
  it("reports never for a missing timestamp", () => expect(relative(null)).toBe("jamais"))
  it("formats hours", () => expect(relative(new Date(Date.now() - 5 * 3600_000).toISOString())).toBe("il y a 5 h"))
})

describe("qs", () => {
  it("drops empty values and encodes the rest", () => {
    expect(qs({ q: "data & ops", page: 2, tracked: false, region: "", country: null })).toBe("?q=data+%26+ops&page=2")
  })
  it("returns an empty string with no parameter", () => expect(qs({})).toBe(""))
})

describe("safeNext", () => {
  it("keeps same-origin paths", () => expect(safeNext("/pipeline?status=APPLIED")).toBe("/pipeline?status=APPLIED"))
  it("refuses other origins, including backslash tricks", () => {
    for (const bad of ["//evil.example", "/\\\\evil.example", "/%5C%5Cevil.example", "https://evil.example/x"]) {
      expect(safeNext(bad === "/%5C%5Cevil.example" ? decodeURIComponent(bad) : bad)).toBe("/")
    }
  })
  it("falls back to the home page", () => expect(safeNext(null)).toBe("/"))
})
