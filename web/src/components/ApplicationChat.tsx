import { useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { ChatMessage } from "../types";

interface Props {
  jobId: number;
  company: string;
}

export function ApplicationChat({ jobId, company }: Props) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [updatedNote, setUpdatedNote] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const queryClient = useQueryClient();

  const send = useMutation({
    mutationFn: (history: ChatMessage[]) => api.chat(jobId, history),
    onSuccess: (data) => {
      setMessages((prev) => [...prev, { role: "assistant", content: data.reply }]);
      if (data.materials_updated) {
        setUpdatedNote(true);
        // Refresh the job so the Cover Letter tab shows the edit immediately.
        queryClient.invalidateQueries({ queryKey: ["job", jobId] });
      }
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
        Ask about {company}: paste a screening question, or ask me to edit the cover letter
        (fix the date, shorten it, adjust tone). Everything stays grounded in your resume — nothing invented.
      </div>
      <div className="chat-messages" ref={scrollRef}>
        {messages.map((m, i) => (
          <div key={i} className={`chat-msg chat-msg-${m.role}`}>
            <pre className="chat-msg-text">{m.content}</pre>
          </div>
        ))}
        {updatedNote && (
          <div className="chat-updated">✓ Cover letter updated — see the Cover Letter tab.</div>
        )}
        {send.isPending && <div className="chat-msg chat-msg-assistant"><em>Thinking…</em></div>}
        {send.isError && <div className="chat-error">Error: {String(send.error)}</div>}
      </div>
      <div className="chat-input-row">
        <textarea
          className="chat-input"
          placeholder="e.g. Update the date on my cover letter, or: highlight my LLM API experience"
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
