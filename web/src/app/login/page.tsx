import type { Metadata } from "next";
import { LoginForm } from "./LoginForm";

export const metadata: Metadata = { title: "Sign in" };

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ expired?: string }>;
}) {
  const { expired } = await searchParams;
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
        <LoginForm />
      </div>
    </main>
  );
}
