"use client";

import { useEffect, useRef, useState } from "react";
import {
  createConversation,
  getConversation,
  sendChatMessage,
  type ConversationRead,
} from "@/lib/api";
import MessageInput from "./MessageInput";
import MessageList from "./MessageList";
import styles from "./ChatWindow.module.css";
import type { DisplayMessage } from "./types";

const STORAGE_KEY = "nutrition-assistant-conversation-id";

function readStoredConversationId(): string | null {
  try {
    return localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

function storeConversationId(id: string): void {
  try {
    localStorage.setItem(STORAGE_KEY, id);
  } catch {
    // Per-viewer convenience only -- fine if storage is unavailable.
  }
}

let localIdCounter = 0;
function newLocalId(): string {
  localIdCounter += 1;
  return `local-${localIdCounter}-${Date.now()}`;
}

function toDisplayMessages(conversation: ConversationRead): DisplayMessage[] {
  return conversation.messages.map((message) =>
    message.role === "assistant"
      ? {
          kind: "assistant",
          id: message.id,
          content: message.content,
          claims: message.claims.map((claim) => ({ claim: claim.claim_text, source: null })),
        }
      : { kind: "user", id: message.id, content: message.content }
  );
}

export default function ChatWindow() {
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<DisplayMessage[]>([]);
  const [isInitializing, setIsInitializing] = useState(true);
  const [isSending, setIsSending] = useState(false);
  const [initError, setInitError] = useState<string | null>(null);
  const hasInitialized = useRef(false);

  useEffect(() => {
    if (hasInitialized.current) return;
    hasInitialized.current = true;

    let cancelled = false;

    async function init() {
      const storedId = readStoredConversationId();
      let hydrated = false;

      if (storedId) {
        try {
          const conversation = await getConversation(storedId);
          if (!cancelled) {
            setConversationId(conversation.id);
            setMessages(toDisplayMessages(conversation));
          }
          hydrated = true;
        } catch {
          // Stale/unknown id (e.g. DB reset) -- fall through to start fresh.
        }
      }

      if (!hydrated) {
        try {
          const conversation = await createConversation();
          if (!cancelled) {
            setConversationId(conversation.id);
            storeConversationId(conversation.id);
          }
        } catch {
          if (!cancelled) {
            setInitError("Could not connect to the backend. Please refresh to try again.");
          }
        }
      }

      if (!cancelled) setIsInitializing(false);
    }

    init();
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleSend(text: string) {
    if (!conversationId) return;

    setMessages((prev) => [...prev, { kind: "user", id: newLocalId(), content: text }]);
    setIsSending(true);

    try {
      const response = await sendChatMessage(conversationId, text);
      if (response.type === "answer") {
        setMessages((prev) => [
          ...prev,
          { kind: "assistant", id: newLocalId(), content: response.answer, claims: response.claims },
        ]);
      } else {
        setMessages((prev) => [
          ...prev,
          { kind: "refusal", id: newLocalId(), reason: response.reason, message: response.message },
        ]);
      }
    } catch (error) {
      console.error("Chat request failed:", error);
      setMessages((prev) => [
        ...prev,
        { kind: "error", id: newLocalId(), message: "Something went wrong. Please try again." },
      ]);
    } finally {
      setIsSending(false);
    }
  }

  if (isInitializing) {
    return <div className={styles.chatWindow}><p className={styles.status}>Loading conversation…</p></div>;
  }

  if (initError) {
    return <div className={styles.chatWindow}><p className={styles.error}>{initError}</p></div>;
  }

  return (
    <div className={styles.chatWindow}>
      <MessageList messages={messages} />
      <MessageInput onSend={handleSend} disabled={isSending} />
    </div>
  );
}
