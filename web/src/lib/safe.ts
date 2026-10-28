import "server-only";
import { ApiError } from "./backend";

/** Run a page's data call and keep the error, so one failed panel does not blank the page. */
export async function settle<T>(work: Promise<T>): Promise<{ data: T | null; error: string | null }> {
  try {
    return { data: await work, error: null };
  } catch (error) {
    if (error instanceof ApiError) return { data: null, error: error.message };
    // Not an API error: let the page's error boundary show it. Redirects also come through here.
    throw error;
  }
}
