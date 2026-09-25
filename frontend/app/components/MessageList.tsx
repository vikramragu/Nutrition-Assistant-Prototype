import RefusalNotice from "./RefusalNotice";
import styles from "./MessageList.module.css";
import type { DisplayMessage } from "./types";

type MessageListProps = {
  messages: DisplayMessage[];
};

export default function MessageList({ messages }: MessageListProps) {
  if (messages.length === 0) {
    return (
      <div className={styles.empty}>Ask a food, nutrition, or food-safety question to get started.</div>
    );
  }

  return (
    <div className={styles.list}>
      {messages.map((message) => {
        switch (message.kind) {
          case "user":
            return (
              <div key={message.id} className={`${styles.bubble} ${styles.user}`}>
                {message.content}
              </div>
            );
          case "assistant":
            return (
              <div key={message.id} className={`${styles.bubble} ${styles.assistant}`}>
                <p>{message.content}</p>
                {message.claims.length > 0 && (
                  <details className={styles.claims}>
                    <summary>Claims (unsourced)</summary>
                    <ul>
                      {message.claims.map((claim, index) => (
                        <li key={index}>{claim.claim}</li>
                      ))}
                    </ul>
                  </details>
                )}
              </div>
            );
          case "refusal":
            return <RefusalNotice key={message.id} reason={message.reason} message={message.message} />;
          case "error":
            return (
              <div key={message.id} className={`${styles.bubble} ${styles.error}`}>
                {message.message}
              </div>
            );
        }
      })}
    </div>
  );
}
