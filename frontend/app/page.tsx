import ChatWindow from "./components/ChatWindow";
import SourcesPanel from "./components/SourcesPanel";
import styles from "./page.module.css";

export default function Home() {
  return (
    <main className={styles.main}>
      <h1 className={styles.title}>AI Nutrition Assistant</h1>
      <div className={styles.layout}>
        <ChatWindow />
        <SourcesPanel sources={[]} />
      </div>
    </main>
  );
}
