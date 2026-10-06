const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

// `personalised_guidance` is new in Phase 2.6: population guidance converted into a
// personal target. Adding it here without a label in RefusalNotice would render
// `undefined` in the badge, so the two files change together.
export type ScopeCategory =
  | "calorie_target"
  | "weight_target"
  | "medical_advice"
  | "personalised_guidance";

/** The minimum a citation needs to be checkable by hand (architecture.md §8.2). */
export type DocumentRef = {
  id: string;
  name: string;
  publisher: string;
  /** Null for the three corpus documents that state no publication year. Never invented. */
  year: number | null;
  url: string;
};

export type Citation = {
  chunk_id: string;
  document: DocumentRef;
  section_heading: string | null;
  /** 0 means *not paginated* — the HTML source — not page one. */
  page_from: number | null;
  page_to: number | null;
  /** The chunk text backing this claim, verbatim. */
  quote: string;
};

/**
 * `source` widened from `null` to a required `Citation` in Phase 2.5.
 *
 * Phase 1 typed it `null` to make "always uncited" a type-level guarantee. This makes
 * "always cited" the same kind of guarantee, pointed the other way — the compiler now
 * refuses any render path that forgets to show a source.
 */
export type Claim = {
  claim: string;
  source: Citation;
};

export type DocumentAnswer = {
  document: DocumentRef;
  answer: string;
  claims: Claim[];
};

/**
 * One block per document, never merged. A single top-level `answer` string could not
 * represent "two documents, answered separately" without blending them or picking a
 * winner, and the brief forbids both — architecture.md §8.1 is the note on that change.
 */
export type ChatAnswerResponse = {
  type: "answer";
  document_answers: DocumentAnswer[];
  searched: DocumentRef[];
};

/** The **policy** refusal: the assistant will not. */
export type ChatRefusedResponse = {
  type: "refused";
  reason: ScopeCategory;
  message: string;
};

/**
 * The **coverage** refusal: the corpus does not.
 *
 * A separate member of the union rather than a `reason` code on `refused`, because the two
 * mean opposite things and a user must be able to tell them apart without reading prose.
 * `searched` is always the full corpus, so the user can judge the gap.
 */
export type ChatNotInCorpusResponse = {
  type: "not_in_corpus";
  message: string;
  searched: DocumentRef[];
};

export type ChatResponse = ChatAnswerResponse | ChatRefusedResponse | ChatNotInCorpusResponse;

export type ClaimRead = {
  id: string;
  claim_text: string;
  /** Null only for Phase 1 rows, which have no `chunk_id`. */
  source: Citation | null;
  /** The Phase 1 text column, always null. Kept so a legacy row reads as "no citation". */
  legacy_source: string | null;
};

/**
 * One stored message. For an assistant message, **one document's** answer — a Phase 2 turn
 * persists one row per document, so a two-document turn reloads as three rows.
 */
export type MessageRead = {
  id: string;
  role: string;
  content: string;
  /** Explicit order. `created_at` cannot order a turn: it is written in one transaction. */
  ordinal: number;
  created_at: string;
  /** Derived server-side from the message's claims. Null for user and Phase 1 rows. */
  document: DocumentRef | null;
  claims: ClaimRead[];
};

export type ConversationRead = {
  id: string;
  created_at: string;
  messages: MessageRead[];
};

export type CorpusDocument = DocumentRef & {
  year_source: string;
  retrieval_date: string;
  page_last_updated: string | null;
  chunk_count: number;
};

export type CorpusResponse = {
  documents: CorpusDocument[];
  chunk_count: number;
  embedding_model: string;
};

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function parseOrThrow<T>(res: Response, failureLabel: string): Promise<T> {
  if (!res.ok) {
    throw new ApiError(res.status, `${failureLabel}: ${res.status}`);
  }
  return res.json();
}

export async function createConversation(): Promise<{ id: string }> {
  const res = await fetch(`${API_BASE_URL}/conversations`, { method: "POST" });
  return parseOrThrow(res, "Failed to create conversation");
}

export async function getConversation(conversationId: string): Promise<ConversationRead> {
  const res = await fetch(`${API_BASE_URL}/conversations/${conversationId}`);
  return parseOrThrow(res, "Failed to load conversation");
}

/** What the assistant can see. Powers the sources panel's idle state. */
export async function getCorpus(): Promise<CorpusResponse> {
  const res = await fetch(`${API_BASE_URL}/corpus`);
  return parseOrThrow(res, "Failed to load corpus");
}

export async function sendChatMessage(
  conversationId: string,
  message: string,
  // The brief's second retrieval mode. Carried here so the client matches the wire
  // contract; no UI selects it yet (see prompt note in the Phase 2.7 write-up).
  documentFilter?: string
): Promise<ChatResponse> {
  const res = await fetch(`${API_BASE_URL}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      conversation_id: conversationId,
      message,
      ...(documentFilter ? { document_filter: documentFilter } : {}),
    }),
  });
  return parseOrThrow(res, "Chat request failed");
}
