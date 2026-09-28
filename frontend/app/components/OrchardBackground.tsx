import styles from "./OrchardBackground.module.css";

// Purely decorative, fixed full-screen, behind all content. The same
// detailed tree shape (trunk + three overlapping canopy circles + fruit),
// repeated at a scatter of positions, with one sleepy bird -- kept
// low-opacity and non-interactive so it reads as background texture, not
// an illustration competing with the chat.
const FRUIT_COLORS = [
  "#d1495b", // apple
  "#e8c547", // banana
  "#e08a3c", // orange
  "#8858a8", // grape
  "#4f6fb0", // blueberry
  "#8bc34a", // lime
  "#b4243a", // cherry
  "#6a3b6e", // plum
];

const TREES = [
  { x: 80, y: 320, scale: 0.9 },
  { x: 210, y: 210, scale: 1 },
  { x: 340, y: 370, scale: 0.85 },
  { x: 470, y: 260, scale: 1.05 },
  { x: 600, y: 400, scale: 0.9 },
  { x: 730, y: 230, scale: 1 },
  { x: 860, y: 340, scale: 0.95 },
  { x: 970, y: 250, scale: 1 },
];

// Index 0 (leftmost) sits in the page margin outside the centered content
// column, so it stays visible instead of hiding behind the chat panel.
const SLEEPY_TREE_INDEX = 0;

// Bottom of the trunk in local tree coordinates is y=90 (trunk: y=20, height=70).
const BASES = TREES.map((t) => ({ x: t.x, y: t.y + 90 * t.scale }));

function Tree({ x, y, scale, fruit }: { x: number; y: number; scale: number; fruit: string }) {
  return (
    <g transform={`translate(${x},${y}) scale(${scale})`}>
      <rect x="-6" y="20" width="12" height="70" rx="4" fill="var(--trunk)" />
      <circle cx="0" cy="0" r="42" fill="var(--leaf)" />
      <circle cx="-32" cy="18" r="30" fill="var(--leaf)" />
      <circle cx="32" cy="18" r="30" fill="var(--leaf)" />
      <circle cx="-18" cy="-8" r="6" fill={fruit} />
      <circle cx="16" cy="-18" r="6" fill={fruit} />
      <circle cx="28" cy="12" r="6" fill={fruit} />
      <circle cx="-30" cy="16" r="6" fill={fruit} />
    </g>
  );
}

function GrassTuft({ x, y }: { x: number; y: number }) {
  return (
    <g stroke="var(--leaf)" strokeWidth="2" strokeLinecap="round" fill="none" opacity="0.7">
      <path d={`M ${x},${y} q -2,-10 -5,-13`} />
      <path d={`M ${x},${y} q 0,-12 0,-15`} />
      <path d={`M ${x},${y} q 2,-10 5,-13`} />
    </g>
  );
}

export default function OrchardBackground() {
  const grassPoints = BASES.flatMap((b) => [
    { x: b.x - 16, y: b.y },
    { x: b.x + 16, y: b.y },
  ]);

  const sleepyTree = TREES[SLEEPY_TREE_INDEX];
  const birdX = sleepyTree.x + 18 * sleepyTree.scale;
  const birdY = sleepyTree.y - 40 * sleepyTree.scale;

  return (
    <svg
      className={styles.orchard}
      viewBox="0 0 1000 600"
      preserveAspectRatio="none"
      aria-hidden="true"
      focusable="false"
    >
      {grassPoints.map((g, i) => (
        <GrassTuft key={i} x={g.x} y={g.y} />
      ))}

      {TREES.map((t, i) => (
        <Tree key={i} x={t.x} y={t.y} scale={t.scale} fruit={FRUIT_COLORS[i % FRUIT_COLORS.length]} />
      ))}

      <g className={styles.jerk} style={{ transformOrigin: `${birdX}px ${birdY}px` }}>
        <ellipse cx={birdX} cy={birdY} rx="10" ry="8" fill="var(--trunk)" />
        <polygon points={`${birdX + 9},${birdY} ${birdX + 18},${birdY + 2} ${birdX + 9},${birdY + 5}`} fill="var(--trunk)" />
        <g className={styles.zzz}>
          <text x={birdX + 6} y={birdY - 14} fontSize="11" fill="var(--muted)">Z</text>
          <text x={birdX + 14} y={birdY - 22} fontSize="8" fill="var(--muted)">z</text>
        </g>
      </g>
    </svg>
  );
}
