"use client";

export default function ErrorPage({ error, reset }: { error: Error; reset: () => void }) {
  return (
    <div>
      <h1 className="mb-2 text-2xl font-bold">This page could not load</h1>
      <div className="notice notice-bad" role="alert">
        {error.message || "Something went wrong."}
      </div>
      <button className="btn mt-4" onClick={reset}>
        Try again
      </button>
    </div>
  );
}
