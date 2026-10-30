import type { Metadata } from "next";
import { entraSettings } from "@/lib/entra";
import { SIGN_IN_PROBLEMS } from "@/lib/entra-flow";
import { LoginForm } from "./LoginForm";

export const metadata: Metadata = { title: "Sign in" };

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ expired?: string; error?: string; denied?: string }>;
}) {
  const { expired, error, denied } = await searchParams;
  const settings = entraSettings();
  // Only fixed sentences are ever shown, never text from the address.
  const problem =
    settings.kind === "broken"
      ? SIGN_IN_PROBLEMS.setup
      : denied
        ? SIGN_IN_PROBLEMS.denied
        : error && Object.hasOwn(SIGN_IN_PROBLEMS, error)
          ? SIGN_IN_PROBLEMS[error]
          : null;
  return (
    <main className="mx-auto grid min-h-screen max-w-[960px] items-center gap-12 px-4 py-12 md:grid-cols-[1fr_380px]">
      <div>
        <h1 className="text-5xl font-bold leading-[1.05] tracking-tight">
          Know what your buildings are using, and what to do about it.
        </h1>
        <p className="mt-4 max-w-[48ch] text-lg text-muted">
          EnergyOps reads your meters, finds the hours that do not look right, and gives each answer
          with the figures behind it.
        </p>
      </div>
      <div className="border border-line bg-surface p-6">
        <h2 className="mb-4 text-xl font-semibold">Sign in</h2>
        {expired ? (
          <div className="mb-4">
            <div className="notice notice-warn" role="status">
              Your sign-in ended. Sign in again to carry on.
            </div>
          </div>
        ) : null}
        {problem ? (
          <div className="mb-4">
            <div className="notice notice-bad" role="alert">
              {problem}
            </div>
          </div>
        ) : null}
        {settings.kind === "on" ? (
          <div>
            <p className="hint mb-4">Use the Microsoft account your organisation gave you.</p>
            {/* A plain link on purpose: the browser must leave this site and come back. */}
            {/* eslint-disable-next-line @next/next/no-html-link-for-pages */}
            <a className="btn btn-primary w-full" href="/api/auth/entra/start">
              Sign in with Microsoft
            </a>
          </div>
        ) : settings.kind === "off" ? (
          <LoginForm />
        ) : null}
      </div>
    </main>
  );
}
