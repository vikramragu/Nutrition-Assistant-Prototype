const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export type ScopeCategory = "calorie_target" | "weight_target" | "medical_advice";

export type Claim = {
  claim: string;
  source: null;
};

export type ChatAnswerResponse = {
  type: "answer";
  answer: string;
  claims: Claim[];
};

export type ChatRefusedResponse = {
  type: "refused";
  reason: ScopeCategory;
  message: string;
};

export type ChatResponse = ChatAnswerResponse | ChatRefusedResponse;

export type ClaimRead = {
  id: string;
  claim_text: string;
  source: string | null;
};

export type MessageRead = {
  id: string;
  role: string;
  content: string;
  created_at: string;
  claims: ClaimRead[];
};

export type ConversationRead = {
  id: string;
  created_at: string;
  messages: MessageRead[];
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

export async function sendChatMessage(conversationId: string, message: string): Promise<ChatResponse> {
  const res = await fetch(`${API_BASE_URL}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ conversation_id: conversationId, message }),
  });
  return parseOrThrow(res, "Chat request failed");
}
