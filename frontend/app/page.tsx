import ChatWindow from "./components/ChatWindow";

/**
 * `ChatWindow` renders the whole workspace — header, corpus rail, conversation and sources
 * panel — because all four read the same state: the conversation, the corpus and which
 * citation is selected. Keeping that in one client component leaves this page a server
 * component with nothing to hydrate.
 *
 * The orchard background from Phase 1 is gone: DESIGN.md's layering is tonal, built from
 * flat surface tokens, and a decorative illustration behind three translucent panels
 * fought with the glassmorphism rather than supporting it.
 */
export default function Home() {
  return <ChatWindow />;
}
