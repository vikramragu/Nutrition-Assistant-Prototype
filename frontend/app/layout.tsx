import type { Metadata } from "next";
import { Plus_Jakarta_Sans } from "next/font/google";
import "./globals.css";

// DESIGN.md's type system. `next/font` self-hosts it at build time, so this adds no
// network request to Google and no npm dependency.
const jakarta = Plus_Jakarta_Sans({
  variable: "--font-sans",
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
});

export const metadata: Metadata = {
  title: "Nutrition Assistant — cited answers from official dietary guidance",
  description:
    "Answers drawn only from published dietary guidance, with a checkable citation on every claim.",
};

// Runs before first paint, so a dark-mode user never sees a white flash. It has to be
// inline and blocking: a React effect runs after hydration, by which point the wrong
// theme has already been painted. Wrapped in try/catch because localStorage throws in a
// private window, and a theme preference is not worth a blank page.
const THEME_INIT = `
try {
  var t = localStorage.getItem('nutrition-assistant-theme');
  if (t === 'dark' || t === 'light') document.documentElement.dataset.theme = t;
} catch (e) {}
`;

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={jakarta.variable} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
