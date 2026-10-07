import type { DocumentRef } from "@/lib/api";
import { BookIcon, ExternalIcon } from "./Icon";
import styles from "./NotInCorpusNotice.module.css";

/**
 * The **coverage** refusal: the corpus does not cover this.
 *
 * Deliberately a separate component from `RefusalNotice`, and deliberately styled as
 * information rather than as a boundary: that one says *the assistant will not*, this one
 * says *the documents do not*. A user who cannot tell them apart learns the wrong lesson
 * from each — either that the assistant is being withholding when it simply has no source,
 * or that a topic is off-limits when it is merely absent.
 *
 * Listing what was searched is the point, not a flourish. "I don't know" invites a retry;
 * seven named documents with links tells the reader what this assistant is *for*, and lets
 * them judge the gap themselves.
 */
type NotInCorpusNoticeProps = {
  message: string;
  searched: DocumentRef[];
};

function yearLabel(year: number | null): string {
  return year === null ? "year not stated" : String(year);
}

export default function NotInCorpusNotice({ message, searched }: NotInCorpusNoticeProps) {
  return (
    <div className={styles.notice} role="status">
      <div className={styles.head}>
        <span className={styles.icon}>
          <BookIcon size={18} />
        </span>
        <div className={styles.headBody}>
          <p className={styles.title}>
            <span className={styles.badge}>Not in the guidance</span>
            No document covers this
          </p>
          <p className={styles.message}>{message}</p>
        </div>
      </div>

      {searched.length > 0 && (
        <details className={styles.searched}>
          <summary className={styles.summary}>
            Searched {searched.length} {searched.length === 1 ? "document" : "documents"}
          </summary>
          <ul className={styles.list}>
            {searched.map((document) => (
              <li key={document.id} className={styles.item}>
                <a
                  className={styles.docLink}
                  href={document.url}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  {document.name}
                  <ExternalIcon size={12} />
                </a>
                <span className={styles.provenance}>
                  {document.publisher} · {yearLabel(document.year)}
                </span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
