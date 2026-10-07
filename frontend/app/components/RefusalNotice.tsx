import type { ScopeCategory } from "@/lib/api";
import { ShieldIcon } from "./Icon";
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

/**
 * The **policy** refusal: the assistant will not.
 *
 * Uses DESIGN.md's "Safety Precaution" semantic token — terracotta — while
 * `NotInCorpusNotice` uses the sage/secondary family. They mean opposite things, so they
 * must not look alike: this one is a boundary the assistant is holding, that one is a gap
 * in the documents. A user who confuses them learns the wrong lesson from each.
 */
type RefusalNoticeProps = {
  reason: ScopeCategory;
  message: string;
};

export default function RefusalNotice({ reason, message }: RefusalNoticeProps) {
  return (
    <div className={styles.notice} role="status">
      <span className={styles.icon}>
        <ShieldIcon size={18} />
      </span>
      <div className={styles.body}>
        <p className={styles.title}>
          <span className={styles.badge}>Outside scope</span>
          {CATEGORY_LABELS[reason]}
        </p>
        <p className={styles.message}>{message}</p>
      </div>
    </div>
  );
}
