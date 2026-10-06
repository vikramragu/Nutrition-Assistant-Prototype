import type { DocumentRef } from "@/lib/api";
import styles from "./NotInCorpusNotice.module.css";

/**
 * The **coverage** refusal — the corpus does not cover this.
 *
 * Deliberately a separate component from `RefusalNotice`, and deliberately styled as
 * information rather than as a boundary: that one says *the assistant will not*, this one
 * says *the documents do not*. They mean opposite things, and a user who cannot tell them
 * apart learns the wrong lesson from each — either that the assistant is being withholding
 * when it simply has no source, or that a topic is off-limits when it is merely absent.
 *
 * Listing what was searched is the point rather than a flourish. "I don't know" invites a
 * retry; seven named documents with links tells the user what this assistant is *for*, and
 * lets them judge the gap themselves.
 */
type NotInCorpusNoticeProps = {
  message: string;
  searched: DocumentRef[];
};

export default function NotInCorpusNotice({ message, searched }: NotInCorpusNoticeProps) {
  return (
    <div className={styles.notice} role="status">
      <span className={styles.badge}>Not in the guidance I searched</span>
      <p className={styles.message}>{message}</p>
      {searched.length > 0 && (
        <details className={styles.searched}>
          <summary>
            Searched {searched.length} {searched.length === 1 ? "document" : "documents"}
          </summary>
          <ul>
            {searched.map((document) => (
              <li key={document.id}>
                <a href={document.url} target="_blank" rel="noopener noreferrer">
                  {document.name}
                </a>
                <span className={styles.provenance}>
                  {document.publisher}
                  {document.year === null ? " · year not stated" : ` · ${document.year}`}
                </span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
