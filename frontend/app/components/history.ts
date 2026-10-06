import type { ConversationRead, DocumentAnswer, MessageRead } from "@/lib/api";
import type { DisplayMessage } from "./types";

/**
 * Turn a reloaded conversation back into the timeline shape the live path produces.
 *
 * Extracted from `ChatWindow` because it is the only non-trivial logic in the frontend and
 * the only part whose failure is invisible: a bug here shows up as two documents merged
 * into one block, or an answer that loses its citations after a refresh, neither of which
 * raises anything. Being a plain module with no JSX, it can be run and checked directly.
 */

/**
 * Rebuild one document's answer from a stored assistant row.
 *
 * The row carries its document (derived server-side from its claims) and its claims with
 * their citations, so nothing is reconstructed by guesswork. A Phase 1 row has neither and
 * returns null: it is skipped rather than rendered as an uncited block. There are no such
 * rows in this database, but inventing a citation for one would be the worst available
 * failure, and dropping it is the safe direction.
 */
export function toDocumentAnswer(message: MessageRead): DocumentAnswer | null {
  if (message.document === null) return null;
  const cited = message.claims.filter((claim) => claim.source !== null);
  if (cited.length === 0) return null;
  return {
    document: message.document,
    answer: message.content,
    claims: cited.map((claim) => ({ claim: claim.claim_text, source: claim.source! })),
  };
}

/**
 * Group a reloaded conversation into turns.
 *
 * The backend persists one assistant row **per document**, so a two-document turn comes
 * back as three rows: the question and two answers. Consecutive assistant rows belong to
 * one turn — `ordinal` is contiguous and a user row always opens a new one — so folding
 * them here lets a reloaded turn render through exactly the same path as a live one. The
 * alternative, a second render path for history, is how "the citations disappear after a
 * refresh" bugs happen.
 */
export function toDisplayMessages(conversation: ConversationRead): DisplayMessage[] {
  const display: DisplayMessage[] = [];

  for (const message of conversation.messages) {
    if (message.role !== "assistant") {
      display.push({ kind: "user", id: message.id, content: message.content });
      continue;
    }

    const documentAnswer = toDocumentAnswer(message);
    if (documentAnswer === null) continue;

    const previous = display[display.length - 1];
    if (previous?.kind === "assistant") {
      previous.documentAnswers.push(documentAnswer);
    } else {
      display.push({ kind: "assistant", id: message.id, documentAnswers: [documentAnswer] });
    }
  }

  return display;
}

/**
 * The newest answer, so the sources panel is useful without a click.
 *
 * The brief asks the panel to show the passages behind "the selected answer"; defaulting
 * the selection to the most recent one means a user who never clicks a marker still sees
 * where the answer came from.
 */
export function latestAnsweredMessageId(display: DisplayMessage[]): string | null {
  for (let i = display.length - 1; i >= 0; i -= 1) {
    const candidate = display[i];
    if (candidate.kind === "assistant" && candidate.documentAnswers.length > 0) {
      return candidate.id;
    }
  }
  return null;
}
