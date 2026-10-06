"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { createConversation, getConversation, sendChatMessage } from "@/lib/api";
import { latestAnsweredMessageId, toDisplayMessages } from "./history";
import MessageInput from "./MessageInput";
import MessageList from "./MessageList";
import SourcesPanel from "./SourcesPanel";
import styles from "./ChatWindow.module.css";
import { citationsOf, type CitationSelection, type DisplayMessage } from "./types";

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

export default function ChatWindow() {
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<DisplayMessage[]>([]);
  const [isInitializing, setIsInitializing] = useState(true);
  const [isSending, setIsSending] = useState(false);
  const [initError, setInitError] = useState<string | null>(null);
  // Which passage the panel is showing. Lives here because `messages` does: the panel and
  // the message list are reading the same turn from two angles, and splitting the two
  // pieces of state would mean keeping them in step by hand.
  const [selection, setSelection] = useState<CitationSelection | null>(null);
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
            const display = toDisplayMessages(conversation);
            setMessages(display);
            const latest = latestAnsweredMessageId(display);
            if (latest !== null) setSelection({ messageId: latest, index: 0 });
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

  const selectedCitations = useMemo(() => {
    if (selection === null) return [];
    const selected = messages.find((message) => message.id === selection.messageId);
    return selected ? citationsOf(selected) : [];
  }, [messages, selection]);

  async function handleSend(text: string) {
    if (!conversationId) return;

    setMessages((prev) => [...prev, { kind: "user", id: newLocalId(), content: text }]);
    setIsSending(true);

    try {
      const response = await sendChatMessage(conversationId, text);

      if (response.type === "answer") {
        const id = newLocalId();
        setMessages((prev) => [
          ...prev,
          { kind: "assistant", id, documentAnswers: response.document_answers },
        ]);
        // Point the panel at the answer that just arrived.
        setSelection({ messageId: id, index: 0 });
      } else if (response.type === "not_in_corpus") {
        setMessages((prev) => [
          ...prev,
          {
            kind: "not_in_corpus",
            id: newLocalId(),
            message: response.message,
            searched: response.searched,
          },
        ]);
        // A coverage refusal has no passages. Clearing the selection sends the panel back
        // to the corpus list, which is the most useful thing it can show at that moment:
        // the user just asked for something outside it.
        setSelection(null);
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

  // The panel renders in every state, including while the conversation loads, so it can
  // answer "what can this thing see?" before anything else works. It is a sibling of the
  // chat column in the page's flex layout rather than a child of it, which is why this
  // component returns a fragment: the shared selection state lives next to `messages`, and
  // lifting both into a new wrapper would have been a larger change than the panel needs.
  const panel = (
    <SourcesPanel
      citations={selectedCitations}
      selectedIndex={selection?.index ?? null}
      onSelect={(index) =>
        setSelection(selection === null ? null : { messageId: selection.messageId, index })
      }
    />
  );

  if (isInitializing) {
    return (
      <>
        <div className={styles.chatWindow}>
          <p className={styles.status}>Loading conversation…</p>
        </div>
        {panel}
      </>
    );
  }

  if (initError) {
    return (
      <>
        <div className={styles.chatWindow}>
          <p className={styles.error}>{initError}</p>
        </div>
        {panel}
      </>
    );
  }

  return (
    <>
      <div className={styles.chatWindow}>
        <MessageList
          messages={messages}
          selection={selection}
          onSelectCitation={setSelection}
        />
        <MessageInput onSend={handleSend} disabled={isSending} />
      </div>
      {panel}
    </>
  );
}
