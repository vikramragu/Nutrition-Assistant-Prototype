import type { ScopeCategory } from "@/lib/api";
import styles from "./RefusalNotice.module.css";

// `Record<ScopeCategory, string>` is load-bearing: adding a category to the union without
// a label here is a compile error rather than an `undefined` in the badge. That is how
// `personalised_guidance` got a label when Phase 2.6 added it.
const CATEGORY_LABELS: Record<ScopeCategory, string> = {
  calorie_target: "Calorie target",
  weight_target: "Weight target",
  medical_advice: "Medical advice",
  personalised_guidance: "Personal recommendation",
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
