"use client";

import { useEffect, useRef, type FormEvent, type KeyboardEvent } from "react";
import { SendIcon } from "./Icon";
import styles from "./MessageInput.module.css";

/**
 * The docked composer.
 *
 * The design's version has a file-attachment button and a row of corpus badges. The
 * attachment is dropped — there is no upload endpoint and a button that does nothing is
 * worse than no button — and the corpus badges moved to the left rail, where the same list
 * is already shown in full rather than truncated to four pills.
 *
 * `value` is lifted to the parent so the empty-state starter questions can fill the box.
 */
type MessageInputProps = {
  value: string;
  onChange: (value: string) => void;
  onSend: (text: string) => void;
  disabled: boolean;
};

const MAX_ROWS_HEIGHT = 180;

export default function MessageInput({
  value,
  onChange,
  onSend,
  disabled,
}: MessageInputProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Grow with the content up to a cap, then scroll. Done by measuring rather than by
  // counting newlines, which gets wrapping wrong at every width.
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, MAX_ROWS_HEIGHT)}px`;
  }, [value]);

  function submit() {
    const trimmed = value.trim();
    if (!trimmed || disabled) return;
    onSend(trimmed);
    onChange("");
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    submit();
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submit();
    }
  }

  return (
    <div className={styles.dock}>
      <form className={styles.composer} onSubmit={handleSubmit}>
        <textarea
          ref={textareaRef}
          className={styles.textarea}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Ask about food, nutrition, or food safety…"
          disabled={disabled}
          rows={1}
          aria-label="Your question"
        />
        <button
          type="submit"
          className={styles.send}
          disabled={disabled || !value.trim()}
          aria-label="Send question"
        >
          <SendIcon size={18} />
        </button>
      </form>
      <p className={styles.disclaimer}>
        General information only, drawn from published guidance — not medical advice. For
        anything personal, talk to a qualified professional.
      </p>
    </div>
  );
}
