import { expect, test } from "@playwright/test";
import { HARBOUR_MANAGER, PLATFORM, signIn } from "./helpers";

test("platform staff land on the agent registry and see no customer data", async ({ page }) => {
  await signIn(page, PLATFORM);
  await expect(page.getByRole("heading", { name: "Agents", level: 1 })).toBeVisible();
  const nav = page.getByRole("navigation", { name: "Main" });
  await expect(nav.getByRole("link", { name: "Organisations" })).toBeVisible();
  await expect(nav.getByRole("link", { name: "Overview" })).toHaveCount(0);
  await page.goto("/tickets");
  await expect(page.getByRole("heading", { name: "Tickets" })).toBeVisible();
  await expect(page.getByText(/does not allow this|view_data/)).toBeVisible();
});

test("a customer cannot open the platform pages", async ({ page }) => {
  await signIn(page, HARBOUR_MANAGER);
  await page.goto("/admin/agents");
  await expect(page.getByText(/does not allow this|admin_platform/)).toBeVisible();
});

test("an agent can be edited, tried in the playground and reset", async ({ page }) => {
  await signIn(page, PLATFORM);
  await page.getByRole("link", { name: "EnergyOps Savings Advisor" }).click();
  await expect(page.getByRole("tab", { name: "Basic" })).toHaveAttribute("aria-selected", "true");

  await page.getByRole("tab", { name: "Configuration" }).click();
  await page.getByLabel("Temperature").fill("0.3");
  await page.getByRole("tab", { name: "Guardrails" }).click();
  await page.getByLabel("Block questions that contain certain words").check();
  await page.getByLabel("Blocked words or phrases").fill("secret plan");
  await page.getByRole("button", { name: "Save settings" }).click();
  await expect(page.getByText(/Settings saved/)).toBeVisible();

  await page.reload();
  await page.getByRole("tab", { name: "Configuration" }).click();
  await expect(page.getByLabel("Temperature")).toHaveValue("0.3");

  await page.getByRole("tab", { name: "Runs" }).click();
  await page.getByRole("button", { name: "Run agent" }).click();
  await expect(page.getByText(/This answer came from the built-in test model/).first()).toBeVisible();

  await page.getByRole("tab", { name: "Basic" }).click();
  await page.getByRole("button", { name: "Reset to built-in settings" }).click();
  await page.waitForLoadState("load");
  await page.getByRole("tab", { name: "Configuration" }).click();
  await expect(page.getByLabel("Temperature")).toHaveValue("");

  // Reset also switches the agent back to draft. Put the local demo setting back so the
  // customer tests that follow can still use it.
  await page.getByRole("tab", { name: "Basic" }).click();
  await page.getByLabel("Status").selectOption("active");
  await page.getByRole("tab", { name: "Configuration" }).click();
  await page.getByLabel("Model provider").selectOption("test");
  await page.getByRole("button", { name: "Save settings" }).click();
  await expect(page.getByText(/Settings saved/)).toBeVisible();
});

test("an organisation can be created and suspended", async ({ page }) => {
  await signIn(page, PLATFORM);
  await page.getByRole("link", { name: "Organisations" }).click();
  await page.getByLabel("Organisation name").fill("Tidewater Care");
  await page.getByLabel("Short name").fill("tidewater-care");
  await page.getByLabel("First administrator's name").fill("Tara Tide");
  await page.getByLabel("First administrator's email").fill("tara@tidewater.example");
  await page.getByRole("button", { name: "Create organisation" }).click();
  await expect(page.getByText(/Tidewater Care was created/)).toBeVisible();
  const row = page.getByRole("row", { name: /Tidewater Care/ });
  await row.getByRole("button", { name: "Suspend" }).click();
  await expect(row.getByText("suspended")).toBeVisible();
});
