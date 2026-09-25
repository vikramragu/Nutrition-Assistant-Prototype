import type { ScopeCategory } from "@/lib/api";
import styles from "./RefusalNotice.module.css";

const CATEGORY_LABELS: Record<ScopeCategory, string> = {
  calorie_target: "Calorie target",
  weight_target: "Weight target",
  medical_advice: "Medical advice",
};

type RefusalNoticeProps = {
  reason: ScopeCategory;
  message: string;
};

export default function RefusalNotice({ reason, message }: RefusalNoticeProps) {
  return (
    <div className={styles.notice} role="status">
      <span className={styles.badge}>{CATEGORY_LABELS[reason]} — outside scope</span>
      <p>{message}</p>
    </div>
  );
}
