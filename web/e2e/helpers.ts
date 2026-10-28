import { expect, type Page } from "@playwright/test";

export async function signIn(page: Page, email: string) {
  await page.goto("/login");
  await page.getByLabel("Email address").fill(email);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).not.toHaveURL(/login/);
}

export const HARBOUR_ADMIN = "admin@harbour.example";
export const HARBOUR_MANAGER = "manager@harbour.example";
export const HARBOUR_TECH = "tech@harbour.example";
export const HARBOUR_VIEWER = "viewer@harbour.example";
export const NORTH_MANAGER = "manager@northside.example";
export const PLATFORM = "platform@energyops.example";
