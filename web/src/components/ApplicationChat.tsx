import { useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { ChatMessage } from "../types";

interface Props {
  jobId: number;
  company: string;
}

const STARTERS = [
  "Give me a strong 60-second introduction for this role.",
  "What are my strongest fits and honest gaps for this role?",
  "Write a concise recruiter outreach message for this position.",
  "Prepare three likely interview questions with grounded answers.",
  "Improve my cover letter for this role and make it more specific.",
  "Refocus my tailored resume on the most relevant experience for this job.",
];

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

  const selectStarter = (prompt: string) => {
    if (send.isPending) return;
    setInput(prompt);
  };

  return (
    <div className="chat">
      <div className="chat-intro">
        Your application copilot for {company}. Ask for answers, interview prep, outreach, company-specific
        positioning, document rewrites, or strategy. It writes freely, while keeping career claims truthful.
      </div>
      {messages.length === 0 && (
        <div className="chat-starters" aria-label="Suggested prompts">
          {STARTERS.map((starter) => (
            <button key={starter} onClick={() => selectStarter(starter)} disabled={send.isPending}>
              {starter}
            </button>
          ))}
        </div>
      )}
      <div className="chat-messages" ref={scrollRef}>
        {messages.map((m, i) => (
          <div key={i} className={`chat-msg chat-msg-${m.role}`}>
            <pre className="chat-msg-text">{m.content}</pre>
          </div>
        ))}
        {updatedNote && (
          <div className="chat-updated">✓ Application materials updated — open the Cover Letter or Resume Tailoring tab to review and download them.</div>
        )}
        {send.isPending && <div className="chat-msg chat-msg-assistant"><em>Thinking…</em></div>}
        {send.isError && <div className="chat-error">Error: {String(send.error)}</div>}
      </div>
      <div className="chat-input-row">
        <textarea
          className="chat-input"
          placeholder="Ask anything about this application — write, analyse, practise, or update my materials…"
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
      <div className="chat-hint">⌘/Ctrl + Enter to send · Review every generated answer before submitting.</div>
    </div>
  );
}
