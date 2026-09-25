import type { Claim, ScopeCategory } from "@/lib/api";

// Local display-only timeline shape. Distinct from the backend's persisted
// `messages` table: a refusal is never persisted as a message (it lives in
// scope_refusals instead), but it still needs a place in the live chat timeline.
export type DisplayMessage =
  | { kind: "user"; id: string; content: string }
  | { kind: "assistant"; id: string; content: string; claims: Claim[] }
  | { kind: "refusal"; id: string; reason: ScopeCategory; message: string }
  | { kind: "error"; id: string; message: string };
