import styles from "./OrchardBackground.module.css";

// Purely decorative, fixed full-screen, behind all content. A winding path
// with a scatter of small flat trees along it, each a different fruit --
// kept low-opacity and non-interactive so it reads as background texture.
const FRUIT_COLORS = [
  "#d1495b", // apple
  "#e8c547", // banana
  "#e08a3c", // orange
  "#8858a8", // grape
  "#4f6fb0", // blueberry
  "#8bc34a", // lime
  "#b4243a", // cherry
  "#f2a488", // peach
  "#6a3b6e", // plum
  "#d9e05b", // lemon
];

const TREES = [
  { x: 60, y: 300, scale: 1 },
  { x: 140, y: 180, scale: 0.75 },
  { x: 220, y: 430, scale: 0.9 },
  { x: 300, y: 300, scale: 1.05 },
  { x: 380, y: 190, scale: 0.8 },
  { x: 460, y: 440, scale: 1 },
  { x: 540, y: 300, scale: 0.85 },
  { x: 620, y: 180, scale: 1.1 },
  { x: 700, y: 430, scale: 0.75 },
  { x: 780, y: 300, scale: 0.95 },
  { x: 860, y: 190, scale: 1 },
  { x: 920, y: 420, scale: 0.8 },
  { x: 970, y: 300, scale: 0.9 },
];

function MiniTree({ x, y, scale, fruit }: { x: number; y: number; scale: number; fruit: string }) {
  return (
    <g transform={`translate(${x},${y}) scale(${scale})`}>
      <rect x="-4" y="10" width="8" height="26" rx="3" fill="var(--trunk)" />
      <circle cx="0" cy="0" r="20" fill="var(--leaf)" />
      <circle cx="-8" cy="4" r="4" fill={fruit} />
      <circle cx="9" cy="-3" r="4" fill={fruit} />
    </g>
  );
}

export default function OrchardBackground() {
  return (
    <svg
      className={styles.orchard}
      viewBox="0 0 1000 600"
      preserveAspectRatio="none"
      aria-hidden="true"
      focusable="false"
    >
      <path
        d="M -20,320 C 80,150 160,480 260,320 C 360,160 440,470 540,320 C 640,160 720,470 820,320 C 900,180 950,420 1020,320"
        stroke="var(--trunk)"
        strokeWidth="3"
        strokeDasharray="2 10"
        strokeLinecap="round"
        fill="none"
        opacity="0.5"
      />
      {TREES.map((t, i) => (
        <MiniTree key={i} x={t.x} y={t.y} scale={t.scale} fruit={FRUIT_COLORS[i % FRUIT_COLORS.length]} />
      ))}
    </svg>
  );
}
