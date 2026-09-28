import styles from "./FruitTreeBackground.module.css";

// Purely decorative, fixed to a corner of the viewport, behind all content.
// Kept intentionally small and low-opacity so it reads as background texture,
// not an illustration competing with the chat.
export default function FruitTreeBackground() {
  return (
    <svg
      className={styles.tree}
      viewBox="0 0 220 220"
      aria-hidden="true"
      focusable="false"
    >
      <ellipse cx="70" cy="190" rx="46" ry="10" fill="var(--leaf)" opacity="0.3" />

      <rect x="64" y="120" width="12" height="70" rx="4" fill="var(--trunk)" />

      <circle cx="70" cy="100" r="42" fill="var(--leaf)" />
      <circle cx="38" cy="118" r="30" fill="var(--leaf)" />
      <circle cx="102" cy="118" r="30" fill="var(--leaf)" />

      <circle cx="52" cy="92" r="6" fill="var(--fruit)" />
      <circle cx="86" cy="82" r="6" fill="var(--fruit)" />
      <circle cx="98" cy="112" r="6" fill="var(--fruit)" />
      <circle cx="40" cy="116" r="6" fill="var(--fruit)" />

      <g className={styles.flutter}>
        <ellipse cx="150" cy="70" rx="9" ry="6" fill="var(--butterfly)" />
        <ellipse cx="166" cy="70" rx="9" ry="6" fill="var(--butterfly)" />
      </g>
    </svg>
  );
}
