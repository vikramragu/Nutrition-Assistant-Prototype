import ChatWindow from "./components/ChatWindow";
import FruitTreeBackground from "./components/FruitTreeBackground";
import LeafIcon from "./components/LeafIcon";
import SourcesPanel from "./components/SourcesPanel";
import styles from "./page.module.css";

export default function Home() {
  return (
    <>
      <FruitTreeBackground />
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
