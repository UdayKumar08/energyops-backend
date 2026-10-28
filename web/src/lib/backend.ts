import "server-only";
import { redirect } from "next/navigation";
import { backendUrl, readToken } from "./session";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function detailOf(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string") return body.detail;
  } catch {
    // not JSON
  }
  return `The server answered ${response.status}.`;
}

/** Call the backend as the signed-in person. A missing or expired sign-in goes to /login. */
export async function backend<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = await readToken();
  if (token === null) redirect("/login");
  const response = await fetch(backendUrl() + path, {
    ...init,
    cache: "no-store",
    headers: { ...init.headers, Authorization: `Bearer ${token}` },
  });
  if (response.status === 401) redirect("/login?expired=1");
  if (!response.ok) throw new ApiError(response.status, await detailOf(response));
  return (await response.json()) as T;
}

/** Like backend(), but a 403 or 404 gives null so a page can show a plain "not available". */
export async function backendOrNull<T>(path: string): Promise<T | null> {
  try {
    return await backend<T>(path);
  } catch (error) {
    if (error instanceof ApiError && (error.status === 403 || error.status === 404)) return null;
    throw error;
  }
}
