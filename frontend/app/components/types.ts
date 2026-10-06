import type { Citation, DocumentAnswer, DocumentRef, ScopeCategory } from "@/lib/api";

// Local display-only timeline shape. Distinct from the backend's persisted `messages`
// table in two ways now:
//
//  - Neither refusal is persisted as a message. A policy refusal lives in
//    `scope_refusals`; a coverage refusal lives in `retrievals`. Both still need a place
//    in the live chat timeline.
//  - One `assistant` entry is one **turn**, holding every document that answered it. The
//    backend persists one row per document, so reloading groups them back together
//    (`groupAssistantTurns` in ChatWindow). Live and reloaded turns therefore render
//    through exactly the same path, which is what makes "refresh keeps the citations"
//    something other than a second code path to get right.
export type DisplayMessage =
  | { kind: "user"; id: string; content: string }
  | { kind: "assistant"; id: string; documentAnswers: DocumentAnswer[] }
  | { kind: "refusal"; id: string; reason: ScopeCategory; message: string }
  | { kind: "not_in_corpus"; id: string; message: string; searched: DocumentRef[] }
  | { kind: "error"; id: string; message: string };

/**
 * Which citation the sources panel is showing.
 *
 * `index` is into the turn's citations **flattened in render order** across its documents,
 * which is also the number shown in the inline marker. Claims arrive as standalone
 * statements rather than as offsets into the answer prose, so a marker cannot be placed
 * mid-sentence without guessing where it belongs — it sits on the claim instead.
 */
export type CitationSelection = {
  messageId: string;
  index: number;
};

/** Every citation of one assistant turn, in the order its markers are numbered. */
export function citationsOf(message: DisplayMessage): Citation[] {
  if (message.kind !== "assistant") return [];
  return message.documentAnswers.flatMap((answer) => answer.claims.map((claim) => claim.source));
}
