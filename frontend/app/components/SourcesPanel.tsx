"use client";

import type { Citation } from "@/lib/api";
import { BookIcon, ExternalIcon } from "./Icon";
import styles from "./SourcesPanel.module.css";

/**
 * The right rail: the passages behind the selected answer.
 *
 * The design's source cards carry a relevance percentage ("96% Rel") and the panel header
 * a cosine threshold and an "Embeddings Coherence" meter. None of that is shown here, and
 * the reason is not laziness — the chat response does not contain a score, and the one we
 * could expose is a cosine similarity, which is not a confidence. Printing "96%" next to a
 * citation invites a reader to treat it as *how likely this is true*, when it measures how
 * near two vectors are. On a product whose whole claim is that you can check the source,
 * a plausible-looking fabricated number is the worst possible ornament.
 *
 * What replaces it is the thing that is real: the quoted passage, its section heading, its
 * page span, and a link to the document.
 *
 * It cannot show an empty state while an answer is selected — every shipped claim carries
 * a citation, enforced by the backend's citation validator — so the only empty state here
 * is "nothing selected yet".
 */
type SourcesPanelProps = {
  citations: Citation[];
  selectedIndex: number | null;
  onSelect: (index: number) => void;
};

function locationLabel(citation: Citation): string | null {
  // page 0 means *not paginated* — the HTML source. "page 0" is wrong and "page 1" is a
  // fabrication, so it gets no page line at all.
  if (!citation.page_from) return null;
  if (citation.page_to && citation.page_to !== citation.page_from) {
    return `pp. ${citation.page_from}–${citation.page_to}`;
  }
  return `p. ${citation.page_from}`;
}

function yearLabel(year: number | null): string {
  return year === null ? "year not stated" : String(year);
}

export default function SourcesPanel({
  citations,
  selectedIndex,
  onSelect,
}: SourcesPanelProps) {
  return (
    <aside className={styles.panel} aria-label="Sources for the selected answer">
      <div className={styles.head}>
        <div className={styles.headRow}>
          <BookIcon size={18} className={styles.headIcon} />
          <h2 className={styles.title}>Grounding sources</h2>
          {citations.length > 0 && (
            <span className={styles.badge}>{citations.length}</span>
          )}
        </div>
        <p className={styles.subtitle}>
          {citations.length > 0
            ? "Every claim above is quoted from one of these passages."
            : "Select an answer to see the passages behind it."}
        </p>
      </div>

      {citations.length === 0 ? (
        <p className={styles.empty}>
          Nothing selected yet. Ask a question, then use a numbered marker in the answer to
          jump to the passage it came from.
        </p>
      ) : (
        <ol className={styles.stack}>
          {citations.map((citation, index) => {
            const isSelected = index === selectedIndex;
            const location = locationLabel(citation);
            return (
              <li
                key={`${citation.chunk_id}-${index}`}
                id={`source-card-${index}`}
                className={isSelected ? styles.cardSelected : styles.card}
              >
                <button
                  type="button"
                  className={styles.cardButton}
                  onClick={() => onSelect(index)}
                  aria-current={isSelected}
                >
                  <span className={styles.cardHead}>
                    <span className={isSelected ? styles.markerSelected : styles.marker}>
                      {index + 1}
                    </span>
                    <span className={styles.docName}>{citation.document.name}</span>
                  </span>
                  <span className={styles.provenance}>
                    {citation.document.publisher} · {yearLabel(citation.document.year)}
                  </span>
                  <q className={styles.quote}>{citation.quote}</q>
                </button>
                <div className={styles.cardFoot}>
                  <span className={styles.section}>
                    {citation.section_heading ? `§ ${citation.section_heading}` : "No section"}
                    {location ? ` · ${location}` : ""}
                  </span>
                  <a
                    className={styles.sourceLink}
                    href={citation.document.url}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    Open source
                    <ExternalIcon size={13} />
                  </a>
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </aside>
  );
}
