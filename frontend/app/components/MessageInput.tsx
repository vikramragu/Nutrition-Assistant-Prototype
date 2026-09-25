"use client";

import { useState, type FormEvent, type KeyboardEvent } from "react";
import styles from "./MessageInput.module.css";

type MessageInputProps = {
  onSend: (text: string) => void;
  disabled: boolean;
};

export default function MessageInput({ onSend, disabled }: MessageInputProps) {
  const [value, setValue] = useState("");

  function submit() {
    const trimmed = value.trim();
    if (!trimmed || disabled) return;
    onSend(trimmed);
    setValue("");
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
    <form className={styles.form} onSubmit={handleSubmit}>
      <textarea
        className={styles.textarea}
        value={value}
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={handleKeyDown}
        placeholder="Ask about food, nutrition, or food safety…"
        disabled={disabled}
        rows={2}
      />
      <button type="submit" className={styles.button} disabled={disabled || !value.trim()}>
        Send
      </button>
    </form>
  );
}
