"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  createConversation,
  getConversation,
  getCorpus,
  sendChatMessage,
  type CorpusResponse,
} from "@/lib/api";
import AppHeader from "./AppHeader";
import CorpusRail from "./CorpusRail";
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

/**
 * Owns the conversation, the corpus and the citation selection, and lays out the three
 * columns the design calls for.
 *
 * Selection lives here because `messages` does: the stream and the sources panel read the
 * same turn from two angles, and splitting the two pieces of state would mean keeping them
 * in step by hand.
 */
export default function ChatWindow() {
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<DisplayMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [isInitializing, setIsInitializing] = useState(true);
  const [isSending, setIsSending] = useState(false);
  const [initError, setInitError] = useState<string | null>(null);
  const [selection, setSelection] = useState<CitationSelection | null>(null);
  const [corpus, setCorpus] = useState<CorpusResponse | null>(null);
  const [corpusError, setCorpusError] = useState(false);
  const hasInitialized = useRef(false);
  const streamEndRef = useRef<HTMLDivElement>(null);

  /**
   * Start-up: load the corpus, and either rehydrate the stored conversation or create one.
   *
   * `hasInitialized` makes this run once. There is deliberately **no `cancelled` flag**,
   * and that is the fix for a bug inherited from Phase 1 that made the app hang on
   * "Loading…" forever in development:
   *
   *   1. the effect runs, sets the guard, starts the request, returns a cleanup
   *   2. React Strict Mode immediately tears the effect down — cleanup sets `cancelled`
   *   3. Strict Mode runs the effect again; the guard returns early, so nothing restarts
   *   4. the one real request resolves into `if (!cancelled)` branches that are all now
   *      false, so no state is ever set
   *
   * It never showed in production because Strict Mode only double-invokes in development,
   * which is exactly the kind of bug that survives a deploy and greets the next person to
   * run the app locally.
   *
   * Dropping the flag means a result can land after unmount. React 18 removed the warning
   * for that precisely because it was mostly noise, and here the component unmounts only
   * when the page does.
   */
  useEffect(() => {
    if (hasInitialized.current) return;
    hasInitialized.current = true;

    getCorpus()
      .then(setCorpus)
      .catch(() => setCorpusError(true));

    async function init() {
      const storedId = readStoredConversationId();

      if (storedId) {
        try {
          const conversation = await getConversation(storedId);
          setConversationId(conversation.id);
          const display = toDisplayMessages(conversation);
          setMessages(display);
          const latest = latestAnsweredMessageId(display);
          if (latest !== null) setSelection({ messageId: latest, index: 0 });
          setIsInitializing(false);
          return;
        } catch {
          // Stale/unknown id (e.g. a reset database) -- fall through and start fresh.
        }
      }

      try {
        const conversation = await createConversation();
        setConversationId(conversation.id);
        storeConversationId(conversation.id);
      } catch {
        setInitError("Could not reach the assistant. Please refresh to try again.");
      }
      setIsInitializing(false);
    }

    init();
  }, []);

  // Keep the newest turn in view as it arrives.
  useEffect(() => {
    streamEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages.length, isSending]);

  const selectedCitations = useMemo(() => {
    if (selection === null) return [];
    const selected = messages.find((message) => message.id === selection.messageId);
    return selected ? citationsOf(selected) : [];
  }, [messages, selection]);

  async function startNewConversation() {
    try {
      const conversation = await createConversation();
      setConversationId(conversation.id);
      storeConversationId(conversation.id);
      setMessages([]);
      setSelection(null);
      setDraft("");
    } catch {
      setMessages((prev) => [
        ...prev,
        { kind: "error", id: newLocalId(), message: "Could not start a new conversation." },
      ]);
    }
  }

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
        // A coverage refusal has no passages, so the panel goes back to its idle state
        // rather than keeping the previous answer's sources on screen next to it.
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
        { kind: "error", id: newLocalId(), message: "Please try again in a moment." },
      ]);
    } finally {
      setIsSending(false);
    }
  }

  return (
    <div className={styles.shell}>
      <AppHeader
        documentCount={corpus?.documents.length ?? null}
        chunkCount={corpus?.chunk_count ?? null}
        onReset={startNewConversation}
        resetDisabled={isSending || isInitializing || messages.length === 0}
      />

      <div className={styles.columns}>
        <CorpusRail corpus={corpus} error={corpusError} />

        <main className={styles.center}>
          <div className={styles.stream}>
            {isInitializing ? (
              <p className={styles.status}>Loading…</p>
            ) : initError ? (
              <p className={styles.statusError}>{initError}</p>
            ) : (
              <>
                <MessageList
                  messages={messages}
                  selection={selection}
                  onSelectCitation={setSelection}
                  onPickStarter={setDraft}
                />
                {isSending && (
                  <p className={styles.thinking} role="status">
                    Searching the guidance…
                  </p>
                )}
              </>
            )}
            <div ref={streamEndRef} />
          </div>

          <MessageInput
            value={draft}
            onChange={setDraft}
            onSend={handleSend}
            disabled={isSending || isInitializing || !conversationId}
          />
        </main>

        <SourcesPanel
          citations={selectedCitations}
          selectedIndex={selection?.index ?? null}
          onSelect={(index) =>
            setSelection(selection === null ? null : { messageId: selection.messageId, index })
          }
        />
      </div>
    </div>
  );
}
