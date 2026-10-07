"use client";

import LeafIcon from "./LeafIcon";
import ThemeToggle from "./ThemeToggle";
import { RefreshIcon, VerifiedIcon } from "./Icon";
import styles from "./AppHeader.module.css";

/**
 * The design's header carries a status pill reading "RAG Fed: USDA 2026, EFSA & WHO
 * Guidelines Active". Ours says the same kind of thing from real data — the document and
 * passage counts that `GET /corpus` returned — and says nothing while that request is in
 * flight, rather than asserting a corpus it has not yet seen.
 *
 * The design also has "Protocol #884-DIET", a confidence score, a latency readout and a
 * patient avatar. Those are omitted rather than faked: the backend returns no confidence,
 * no latency and no user, and inventing them in the one place a reader looks for
 * trustworthiness would undercut the thing this product is for.
 */
type AppHeaderProps = {
  documentCount: number | null;
  chunkCount: number | null;
  onReset: () => void;
  resetDisabled: boolean;
};

export default function AppHeader({
  documentCount,
  chunkCount,
  onReset,
  resetDisabled,
}: AppHeaderProps) {
  return (
    <header className={styles.header}>
      <div className={styles.brand}>
        <span className={styles.mark}>
          <LeafIcon size={18} />
        </span>
        <span className={styles.wordmark}>Nutrition Assistant</span>
      </div>

      {documentCount !== null && chunkCount !== null && (
        <p className={styles.status}>
          <VerifiedIcon size={15} className={styles.statusIcon} />
          <span>
            Grounded in <strong>{documentCount}</strong> official guidance documents ·{" "}
            <strong>{chunkCount}</strong> passages indexed
          </span>
        </p>
      )}

      <div className={styles.actions}>
        <button
          type="button"
          className={styles.textButton}
          onClick={onReset}
          disabled={resetDisabled}
          title="Start a new conversation"
        >
          <RefreshIcon size={17} />
          <span className={styles.textButtonLabel}>New chat</span>
        </button>
        <ThemeToggle />
      </div>
    </header>
  );
}
