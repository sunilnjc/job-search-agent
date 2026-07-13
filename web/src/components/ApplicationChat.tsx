import { useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { api } from "../api/client";
import type { ChatMessage } from "../types";

interface Props {
  jobId: number;
  company: string;
}

export function ApplicationChat({ jobId, company }: Props) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);

  const send = useMutation({
    mutationFn: (history: ChatMessage[]) => api.chat(jobId, history),
    onSuccess: (data) => {
      setMessages((prev) => [...prev, { role: "assistant", content: data.reply }]);
      queueMicrotask(() => scrollRef.current?.scrollTo(0, scrollRef.current.scrollHeight));
    },
  });

  const handleSend = () => {
    const text = input.trim();
    if (!text || send.isPending) return;
    const next: ChatMessage[] = [...messages, { role: "user", content: text }];
    setMessages(next);
    setInput("");
    send.mutate(next);
  };

  return (
    <div className="chat">
      <div className="chat-intro">
        Paste an application question from {company}. Answers are grounded in your resume and this
        role's drafted materials — nothing is invented.
      </div>
      <div className="chat-messages" ref={scrollRef}>
        {messages.map((m, i) => (
          <div key={i} className={`chat-msg chat-msg-${m.role}`}>
            <pre className="chat-msg-text">{m.content}</pre>
          </div>
        ))}
        {send.isPending && <div className="chat-msg chat-msg-assistant"><em>Thinking…</em></div>}
        {send.isError && (
          <div className="chat-error">Error: {String(send.error)}</div>
        )}
      </div>
      <div className="chat-input-row">
        <textarea
          className="chat-input"
          placeholder="e.g. Please highlight your experience integrating Large Language Models using APIs"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) handleSend();
          }}
          rows={3}
        />
        <button className="chat-send" onClick={handleSend} disabled={send.isPending || !input.trim()}>
          Ask
        </button>
      </div>
      <div className="chat-hint">⌘/Ctrl + Enter to send</div>
    </div>
  );
}
