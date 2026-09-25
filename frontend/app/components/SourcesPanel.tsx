import styles from "./SourcesPanel.module.css";

// Kept as its own component -- purely presentational -- so the future
// milestone that populates claims[].source only has to touch this one file.
export type Source = {
  title: string;
  url: string;
};

type SourcesPanelProps = {
  sources: Source[];
};

export default function SourcesPanel({ sources }: SourcesPanelProps) {
  return (
    <aside className={styles.panel}>
      <h2 className={styles.heading}>Sources</h2>
      {sources.length === 0 ? (
        <p className={styles.empty}>No sources yet — coming in a future milestone.</p>
      ) : (
        <ul className={styles.list}>
          {sources.map((source) => (
            <li key={source.url}>
              <a href={source.url}>{source.title}</a>
            </li>
          ))}
        </ul>
      )}
    </aside>
  );
}
