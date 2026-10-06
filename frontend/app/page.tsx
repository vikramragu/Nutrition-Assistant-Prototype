import ChatWindow from "./components/ChatWindow";
import LeafIcon from "./components/LeafIcon";
import OrchardBackground from "./components/OrchardBackground";
import styles from "./page.module.css";

export default function Home() {
  return (
    <>
      <OrchardBackground />
      <main className={styles.main}>
        <h1 className={styles.title}>
          <LeafIcon />
          AI Nutrition Assistant
        </h1>
        {/* ChatWindow renders the chat column *and* the sources panel as the two flex
            children of this layout. The panel shows the passages behind the selected
            answer, so it needs the same state the message list reads; keeping both in one
            client component avoids a wrapper whose only job is to hold that state, and
            leaves this page a server component. */}
        <div className={styles.layout}>
          <ChatWindow />
        </div>
      </main>
    </>
  );
}
