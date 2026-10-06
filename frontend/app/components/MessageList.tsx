import NotInCorpusNotice from "./NotInCorpusNotice";
import RefusalNotice from "./RefusalNotice";
import styles from "./MessageList.module.css";
import type { CitationSelection, DisplayMessage } from "./types";

/**
 * Four visually distinct states, which is the point rather than a nicety: an answer, a
 * policy refusal, a coverage refusal and a transport error mean four different things, and
 * the user has to act differently on each.
 *
 * An assistant turn renders **one block per document**, each headed by its publisher and
 * year. Two documents must look like two documents: merging them visually would undo the
 * one-call-per-document guarantee the backend goes to some trouble to make structural, and
 * it is the only thing standing between "WHO and the USDA each say this" and "nutrition
 * authorities say this".
 */
type MessageListProps = {
  messages: DisplayMessage[];
  selection: CitationSelection | null;
  onSelectCitation: (selection: CitationSelection) => void;
};

function yearLabel(year: number | null): string {
  return year === null ? "year not stated" : String(year);
}

export default function MessageList({ messages, selection, onSelectCitation }: MessageListProps) {
  if (messages.length === 0) {
    return (
      <div className={styles.empty}>Ask a food, nutrition, or food-safety question to get started.</div>
    );
  }

  return (
    <div className={styles.list}>
      {messages.map((message) => {
        switch (message.kind) {
          case "user":
            return (
              <div key={message.id} className={`${styles.bubble} ${styles.user}`}>
                {message.content}
              </div>
            );

          case "assistant": {
            // Markers are numbered across the whole turn, so [3] means the same passage in
            // the message and in the panel even when it is the first claim of the second
            // document. This counter is why the numbering is computed here rather than per
            // block.
            let markerNumber = 0;
            return (
              <div key={message.id} className={styles.turn}>
                {message.documentAnswers.map((documentAnswer) => (
                  <article key={documentAnswer.document.id} className={styles.documentBlock}>
                    <header className={styles.documentHeader}>
                      <span className={styles.publisher}>{documentAnswer.document.publisher}</span>
                      <span className={styles.year}>{yearLabel(documentAnswer.document.year)}</span>
                    </header>
                    <p className={styles.documentName}>{documentAnswer.document.name}</p>
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
                              onClick={() => onSelectCitation({ messageId: message.id, index })}
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
                {message.documentAnswers.length > 1 && (
                  <p className={styles.separateNote}>
                    {message.documentAnswers.length} documents answered separately. Where they
                    differ, both are shown — neither is presented as the winner.
                  </p>
                )}
              </div>
            );
          }

          case "refusal":
            return <RefusalNotice key={message.id} reason={message.reason} message={message.message} />;

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
              <div key={message.id} className={`${styles.bubble} ${styles.error}`}>
                {message.message}
              </div>
            );
        }
      })}
    </div>
  );
}
