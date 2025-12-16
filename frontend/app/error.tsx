"use client";

import { useEffect } from "react";

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // eslint-disable-next-line no-console
    console.error("App error boundary:", error);
  }, [error]);

  return (
    <div style={{ padding: 16, fontFamily: "Arial, Helvetica, sans-serif" }}>
      <h2 style={{ margin: 0 }}>Something went wrong</h2>
      <p style={{ marginTop: 8, opacity: 0.8 }}>
        If you see a blank page, run <b>npm run dev:clean</b> and refresh (Ctrl+F5).
      </p>
      <pre
        style={{
          marginTop: 12,
          padding: 12,
          background: "#111",
          color: "#eee",
          borderRadius: 8,
          overflow: "auto",
          maxHeight: 280,
        }}
      >
        {error?.message}
      </pre>
      <button
        onClick={reset}
        style={{
          marginTop: 12,
          padding: "8px 12px",
          borderRadius: 8,
          border: "1px solid #999",
          cursor: "pointer",
        }}
      >
        Try again
      </button>
    </div>
  );
}



