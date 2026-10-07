/**
 * The handful of icons this app uses, as inline SVG.
 *
 * The design calls for Material Symbols, which ships as a webfont from Google. That would
 * be a second font request and several hundred KB of glyphs for the eight shapes below —
 * and a render-blocking dependency on fonts.gstatic.com for what are, in the end, eight
 * paths. Inline SVG costs nothing, inherits `currentColor` so it themes for free, and
 * follows the existing `LeafIcon` component.
 *
 * Every icon is decorative: it sits beside a text label, or inside a button that carries
 * its own `aria-label`. Hence `aria-hidden` on all of them — announcing "book" before
 * "Grounding sources" is noise to a screen reader, not information.
 */

type IconProps = {
  size?: number;
  className?: string;
};

function svg(path: React.ReactNode, { size = 20, className }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
      focusable="false"
    >
      {path}
    </svg>
  );
}

export const BookIcon = (p: IconProps) =>
  svg(
    <>
      <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
      <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
    </>,
    p
  );

export const VerifiedIcon = (p: IconProps) =>
  svg(
    <>
      <path d="m12 2 2.4 2.1 3.2-.4 1 3 2.9 1.4-1.1 3 1.1 3-2.9 1.4-1 3-3.2-.4L12 22l-2.4-2.1-3.2.4-1-3L2.5 15.9l1.1-3-1.1-3 2.9-1.4 1-3 3.2.4z" />
      <path d="m9 12 2 2 4-4" />
    </>,
    p
  );

export const InfoIcon = (p: IconProps) =>
  svg(
    <>
      <circle cx="12" cy="12" r="10" />
      <path d="M12 16v-4M12 8h.01" />
    </>,
    p
  );

export const ShieldIcon = (p: IconProps) =>
  svg(<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />, p);

export const AlertIcon = (p: IconProps) =>
  svg(
    <>
      <path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" />
      <path d="M12 9v4M12 17h.01" />
    </>,
    p
  );

export const SendIcon = (p: IconProps) =>
  svg(<path d="M12 19V5M5 12l7-7 7 7" />, p);

export const SunIcon = (p: IconProps) =>
  svg(
    <>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
    </>,
    p
  );

export const MoonIcon = (p: IconProps) =>
  svg(<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />, p);

export const RefreshIcon = (p: IconProps) =>
  svg(
    <>
      <path d="M3 12a9 9 0 0 1 15.5-6.2L21 8" />
      <path d="M21 3v5h-5" />
      <path d="M21 12a9 9 0 0 1-15.5 6.2L3 16" />
      <path d="M3 21v-5h5" />
    </>,
    p
  );

export const ExternalIcon = (p: IconProps) =>
  svg(
    <>
      <path d="M7 17 17 7M9 7h8v8" />
    </>,
    p
  );
