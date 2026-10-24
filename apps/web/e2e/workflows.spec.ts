import { expect, type Page, test } from "@playwright/test"

const PASSWORD = "e2e-password-123"

async function login(page: Page) {
  await page.goto("/login")
  await page.getByLabel("Mot de passe").fill(PASSWORD)
  await page.getByRole("button", { name: /se connecter/i }).click()
  await expect(page.getByRole("heading", { name: /Bonjour|Bonsoir|Bonne nuit/ })).toBeVisible()
}

test.describe.configure({ mode: "serial" })

test("protected pages redirect to login and a wrong password is refused", async ({ page }) => {
  await page.goto("/opportunities")
  await expect(page).toHaveURL(/\/login/)
  await page.getByLabel("Mot de passe").fill("wrong-password")
  await page.getByRole("button", { name: /se connecter/i }).click()
  await expect(page.getByText(/mot de passe incorrect/i)).toBeVisible()
})

test("track an opportunity, then confirm the submission when moving it on the board", async ({ page }) => {
  await login(page)
  await page.getByRole("link", { name: "Opportunités" }).first().click()
  await page.getByRole("link", { name: /Robotics Deployment Intern/ }).click()
  await page.getByRole("button", { name: "Suivre la candidature" }).click()
  await expect(page.getByText(/Ajoutée à tes candidatures|Déjà suivie/).first()).toBeVisible()

  await page.goto("/pipeline")
  const card = page.getByText("Robotics Deployment Intern")
  await expect(card).toBeVisible()
  const target = page.locator('[data-status="APPLIED"]')
  const from = (await card.boundingBox())!
  const to = (await target.boundingBox())!
  await page.mouse.move(from.x + 20, from.y + 10)
  await page.mouse.down()
  await page.mouse.move(from.x + 40, from.y + 20, { steps: 4 })
  await page.mouse.move(to.x + to.width / 2, to.y + 60, { steps: 12 })
  await page.mouse.up()

  await expect(page.getByText("Candidature envoyée ?")).toBeVisible()
  await page.getByRole("button", { name: "Oui, elle est envoyée" }).click()
  await expect(page.getByText(/Formic : Postulé/)).toBeVisible()
  await page.reload()
  await expect(page.locator('[data-status="APPLIED"]').getByText("Robotics Deployment Intern")).toBeVisible()
})

test("add a contact, write a validated message, copy it and record it as sent", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"])
  await login(page)
  await page.goto("/opportunities")
  await page.getByRole("link", { name: /Robotics Deployment Intern/ }).click()
  await page.getByRole("button", { name: "Ajouter", exact: true }).click()
  await page.getByLabel("Nom", { exact: true }).fill("Jane Doe")
  await page.getByLabel("Fonction").fill("University Recruiter")
  await page.getByLabel("Profil LinkedIn").fill("https://www.linkedin.com/company/formic")
  await page.getByRole("button", { name: /ajouter le contact/i }).click()
  await expect(page.getByText(/profil public/)).toBeVisible()
  await page.getByLabel("Profil LinkedIn").fill("https://www.linkedin.com/in/jane-doe-e2e")
  await page.getByRole("button", { name: /ajouter le contact/i }).click()
  await expect(page.getByText("Jane Doe ajouté")).toBeVisible()

  await page.getByRole("link", { name: "Écrire" }).first().click()
  await expect(page.getByRole("heading", { name: "Jane Doe" })).toBeVisible()
  await expect(page.getByText("Aucun message pour l’instant")).toBeVisible()          // nothing is generated or sent without a click
  await page.getByRole("button", { name: "Générer le message" }).click()
  const text = page.locator("textarea[readonly]").first()
  await expect(text).toHaveValue(/Robotics Deployment internship/)
  await expect(page.getByText(/Vérifié :/)).toBeVisible()

  await page.getByRole("button", { name: "Copier le message" }).click()
  const clip = await page.evaluate(() => navigator.clipboard.readText())
  expect(clip).toContain("Robotics Deployment internship")

  await page.getByRole("button", { name: "Marquer comme envoyé" }).click()
  await expect(page.getByText(/Noté comme envoyé/)).toBeVisible()
  await expect(page.getByText("Message envoyé").first()).toBeVisible()
})

test("command palette finds an opportunity", async ({ page }) => {
  await login(page)
  await page.keyboard.press("ControlOrMeta+k")
  await page.getByPlaceholder(/rechercher/i).last().fill("Stripe")
  await page.getByRole("option", { name: /Solutions Engineer Intern/ }).click()
  await expect(page.getByRole("heading", { name: "Solutions Engineer Intern" })).toBeVisible()
})
