"use client";

import { useEffect, useState } from "react";
import { getCorpus, type Citation, type CorpusResponse } from "@/lib/api";
import LeafIcon from "./LeafIcon";
import styles from "./SourcesPanel.module.css";

/**
 * The panel always tells the user what the assistant can see.
 *
 * Two states, and the idle one is the reason this fetches anything: with no answer
 * selected it lists the corpus from `GET /corpus`, so "what can I ask this thing?" is
 * answerable before asking. With an answer selected it shows the passages behind it —
 * the quoted chunk text, not just a link, because a citation you have to go and open is
 * one most people will not check.
 *
 * It cannot show an empty state while an answer is selected: every shipped claim carries a
 * citation (enforced in the backend's citation validator), so a selected answer always has
 * at least one passage to show.
 */
type SourcesPanelProps = {
  /** Citations of the selected turn, flattened in marker order. Empty when idle. */
  citations: Citation[];
  /** Which marker is highlighted, or null when the turn is selected but no single claim. */
  selectedIndex: number | null;
  onSelect: (index: number) => void;
};

function pageLabel(citation: Citation): string | null {
  // 0 means *not paginated* -- the HTML source. Rendering it as "page 0", or silently as
  // "page 1", would put a page number on a web page that has none.
  if (!citation.page_from) return null;
  if (citation.page_to && citation.page_to !== citation.page_from) {
    return `pp. ${citation.page_from}–${citation.page_to}`;
  }
  return `p. ${citation.page_from}`;
}

function yearLabel(year: number | null): string {
  // The brief forbids inventing a year, so say it is missing rather than leaving a gap the
  // reader fills in themselves.
  return year === null ? "year not stated" : String(year);
}

export default function SourcesPanel({ citations, selectedIndex, onSelect }: SourcesPanelProps) {
  const [corpus, setCorpus] = useState<CorpusResponse | null>(null);
  const [corpusError, setCorpusError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getCorpus()
      .then((loaded) => {
        if (!cancelled) setCorpus(loaded);
      })
      .catch(() => {
        if (!cancelled) setCorpusError(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const hasSelection = citations.length > 0;

  return (
    <aside className={styles.panel} aria-label="Sources">
      <h2 className={styles.heading}>
        <LeafIcon size={16} />
        {hasSelection ? "Sources for this answer" : "What I can see"}
      </h2>

      {hasSelection ? (
        <ol className={styles.citations}>
          {citations.map((citation, index) => (
            <li
              key={`${citation.chunk_id}-${index}`}
              className={index === selectedIndex ? styles.citationSelected : styles.citation}
            >
              <button
                type="button"
                className={styles.citationButton}
                onClick={() => onSelect(index)}
                aria-current={index === selectedIndex}
              >
                <span className={styles.marker}>{index + 1}</span>
                <span className={styles.citationBody}>
                  <span className={styles.documentName}>{citation.document.name}</span>
                  <span className={styles.provenance}>
                    {citation.document.publisher} · {yearLabel(citation.document.year)}
                  </span>
                  {citation.section_heading && (
                    <span className={styles.section}>
                      § {citation.section_heading}
                      {pageLabel(citation) ? ` · ${pageLabel(citation)}` : ""}
                    </span>
                  )}
                  <span className={styles.quote}>{citation.quote}</span>
                </span>
              </button>
              <a
                className={styles.sourceLink}
                href={citation.document.url}
                target="_blank"
                rel="noopener noreferrer"
              >
                Open the source document ↗
              </a>
            </li>
          ))}
        </ol>
      ) : corpusError ? (
        <p className={styles.empty}>Could not load the document list.</p>
      ) : corpus === null ? (
        <p className={styles.empty}>Loading the document list…</p>
      ) : (
        <>
          <p className={styles.empty}>
            I answer only from these {corpus.documents.length} documents — {corpus.chunk_count}{" "}
            passages in all. Ask something they cover and the passages behind the answer appear
            here.
          </p>
          <ul className={styles.list}>
            {corpus.documents.map((document) => (
              <li key={document.id} className={styles.listItem}>
                <a href={document.url} target="_blank" rel="noopener noreferrer">
                  {document.name}
                </a>
                <span className={styles.provenance}>
                  {document.publisher} · {yearLabel(document.year)} · {document.chunk_count} passages
                </span>
              </li>
            ))}
          </ul>
        </>
      )}
    </aside>
  );
}
