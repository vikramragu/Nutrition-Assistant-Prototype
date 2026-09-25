"use client";

import { useEffect, useState } from "react";
import { getHealth } from "@/lib/api";

export default function Home() {
  const [status, setStatus] = useState<string>("checking backend...");

  useEffect(() => {
    getHealth()
      .then((res) => setStatus(`backend: ${res.status}`))
      .catch(() => setStatus("backend: unreachable"));
  }, []);

  return (
    <main style={{ padding: 32, fontFamily: "sans-serif" }}>
      <h1>AI Nutrition Assistant</h1>
      <p>{status}</p>
    </main>
  );
}
