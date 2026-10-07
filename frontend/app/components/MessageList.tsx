import NotInCorpusNotice from "./NotInCorpusNotice";
import RefusalNotice from "./RefusalNotice";
import { AlertIcon, VerifiedIcon } from "./Icon";
import styles from "./MessageList.module.css";
import type { CitationSelection, DisplayMessage } from "./types";

/**
 * The dialogue stream.
 *
 * An assistant turn renders **one card per document**, each headed by its publisher and
 * year, with a real gap between cards. Two documents must read as two documents: merging
 * them visually would undo the one-call-per-document guarantee the backend is built
 * around, and it is the only thing standing between "WHO and the USDA each say this" and
 * "nutrition authorities say this".
 *
 * The design puts a single synthesised card per answer with a confidence badge and a
 * retrieval telemetry strip. Ours keeps the card shape and the inline `[n]` markers but
 * drops the telemetry — there is no confidence score, no latency and no relevance
 * percentage in the response, and the one number we could surface is a cosine similarity,
 * which is not a confidence. What survives is the strip's honest half: how many documents
 * answered, and how many read the question and had nothing to add.
 */
type MessageListProps = {
  messages: DisplayMessage[];
  selection: CitationSelection | null;
  onSelectCitation: (selection: CitationSelection) => void;
};

function yearLabel(year: number | null): string {
  return year === null ? "year not stated" : String(year);
}

const STARTERS = [
  "How long can cooked chicken sit out at room temperature?",
  "How much free sugar should an adult eat per day?",
  "Can I reuse oil I have already fried food in?",
];

type EmptyStateProps = { onPick: (question: string) => void };

function EmptyState({ onPick }: EmptyStateProps) {
  return (
    <div className={styles.empty}>
      <h2 className={styles.emptyTitle}>Ask about food, nutrition, or food safety</h2>
      <p className={styles.emptyBody}>
        Every answer is quoted from published guidance and carries a citation you can open.
        When the documents don&rsquo;t cover something, you get a refusal rather than a guess.
      </p>
      <div className={styles.starters}>
        {STARTERS.map((question) => (
          <button
            key={question}
            type="button"
            className={styles.starter}
            onClick={() => onPick(question)}
          >
            {question}
          </button>
        ))}
      </div>
    </div>
  );
}

export default function MessageList({
  messages,
  selection,
  onSelectCitation,
  onPickStarter,
}: MessageListProps & { onPickStarter: (question: string) => void }) {
  if (messages.length === 0) {
    return <EmptyState onPick={onPickStarter} />;
  }

  return (
    <div className={styles.stream}>
      {messages.map((message) => {
        switch (message.kind) {
          case "user":
            return (
              <div key={message.id} className={styles.userRow}>
                <p className={styles.userBubble}>{message.content}</p>
              </div>
            );

          case "assistant": {
            // Markers are numbered across the whole turn, so [3] means the same passage in
            // the answer and in the sources panel even when it is the first claim of the
            // second document. Hence one counter here rather than one per card.
            let markerNumber = 0;
            const documentCount = message.documentAnswers.length;
            return (
              <div key={message.id} className={styles.turn}>
                <div className={styles.telemetry}>
                  <VerifiedIcon size={15} className={styles.telemetryIcon} />
                  <span>
                    Answered from <strong>{documentCount}</strong>{" "}
                    {documentCount === 1 ? "document" : "documents"}, quoted directly
                  </span>
                </div>

                {message.documentAnswers.map((documentAnswer) => (
                  <article key={documentAnswer.document.id} className={styles.docCard}>
                    <header className={styles.docHead}>
                      <span className={styles.publisher}>
                        {documentAnswer.document.publisher}
                      </span>
                      <span className={styles.dot} />
                      <span className={styles.year}>
                        {yearLabel(documentAnswer.document.year)}
                      </span>
                    </header>
                    <p className={styles.docName}>{documentAnswer.document.name}</p>
                    <p className={styles.answer}>{documentAnswer.answer}</p>

                    <ul className={styles.claims}>
                      {documentAnswer.claims.map((claim) => {
                        const index = markerNumber;
                        markerNumber += 1;
                        const isSelected =
                          selection?.messageId === message.id && selection.index === index;
                        return (
                          <li key={`${claim.source.chunk_id}-${index}`} className={styles.claim}>
                            <button
                              type="button"
                              className={isSelected ? styles.markerSelected : styles.marker}
                              onClick={() =>
                                onSelectCitation({ messageId: message.id, index })
                              }
                              aria-label={`Show the source passage for claim ${index + 1}`}
                              aria-pressed={isSelected}
                            >
                              {index + 1}
                            </button>
                            <span className={styles.claimText}>{claim.claim}</span>
                          </li>
                        );
                      })}
                    </ul>
                  </article>
                ))}

                {documentCount > 1 && (
                  <p className={styles.separateNote}>
                    {documentCount} documents answered separately. Where they differ, both are
                    shown — neither is presented as the winner.
                  </p>
                )}
              </div>
            );
          }

          case "refusal":
            return (
              <RefusalNotice
                key={message.id}
                reason={message.reason}
                message={message.message}
              />
            );

          case "not_in_corpus":
            return (
              <NotInCorpusNotice
                key={message.id}
                message={message.message}
                searched={message.searched}
              />
            );

          case "error":
            return (
              <div key={message.id} className={styles.errorCard} role="alert">
                <AlertIcon size={18} className={styles.errorIcon} />
                <div>
                  <p className={styles.errorTitle}>Something went wrong</p>
                  <p className={styles.errorBody}>{message.message}</p>
                </div>
              </div>
            );
        }
      })}
    </div>
  );
}
