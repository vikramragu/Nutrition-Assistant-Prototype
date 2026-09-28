type LeafIconProps = {
  size?: number;
  className?: string;
};

export default function LeafIcon({ size = 20, className }: LeafIconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      className={className}
      aria-hidden="true"
    >
      <path
        d="M20 4C11 4 4 11 4 20c9 0 16-7 16-16Z"
        fill="var(--leaf)"
      />
      <path
        d="M20 4C11 4 4 11 4 20"
        stroke="var(--accent)"
        strokeWidth="1.2"
        strokeLinecap="round"
      />
    </svg>
  );
}
