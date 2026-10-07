"use client";

import type { CorpusResponse } from "@/lib/api";
import { BookIcon } from "./Icon";
import styles from "./CorpusRail.module.css";

/**
 * The left rail: everything the assistant can see, always.
 *
 * The design's left rail is a workspace nav — Clinical Chat, Evidence Corpus, Dietary
 * Protocols, Patient Profiles. Three of those four are screens this product does not have
 * and a backend that cannot serve them, so rather than four dead links the rail becomes
 * the one of them that is real: the corpus.
 *
 * That also resolves a split the old UI fudged. The left rail answers *what can I ask
 * about?* and never changes; the right panel answers *where did this answer come from?*
 * and changes per answer. Previously one panel tried to be both, and had to be empty for
 * one of the two jobs.
 */
type CorpusRailProps = {
  corpus: CorpusResponse | null;
  error: boolean;
};

function yearLabel(year: number | null): string {
  // Three corpus documents state no publication year and the brief forbids inventing one.
  return year === null ? "year not stated" : String(year);
}

export default function CorpusRail({ corpus, error }: CorpusRailProps) {
  return (
    <aside className={styles.rail} aria-label="Guidance documents">
      <div className={styles.head}>
        <BookIcon size={17} className={styles.headIcon} />
        <h2 className={styles.title}>Guidance corpus</h2>
      </div>

      {error ? (
        <p className={styles.note}>Could not load the document list.</p>
      ) : corpus === null ? (
        <p className={styles.note}>Loading…</p>
      ) : (
        <>
          <p className={styles.note}>
            Answers come only from these {corpus.documents.length} documents. Anything they
            don&rsquo;t cover gets a refusal, not a guess.
          </p>
          <ul className={styles.list}>
            {corpus.documents.map((document) => (
              <li key={document.id} className={styles.item}>
                <a
                  className={styles.docLink}
                  href={document.url}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  {document.name}
                </a>
                <span className={styles.meta}>
                  {document.publisher} · {yearLabel(document.year)}
                </span>
                <span className={styles.count}>{document.chunk_count} passages</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </aside>
  );
}
