import ChatWindow from "./components/ChatWindow";
import LeafIcon from "./components/LeafIcon";
import OrchardBackground from "./components/OrchardBackground";
import SourcesPanel from "./components/SourcesPanel";
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
        <div className={styles.layout}>
          <ChatWindow />
          <SourcesPanel sources={[]} />
        </div>
      </main>
    </>
  );
}
