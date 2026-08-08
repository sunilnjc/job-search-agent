import { useCallback, useEffect, useRef, useState } from "react";
import type { Session } from "@supabase/supabase-js";
import { requireSupabase } from "./supabase";

type Resume = {
  id: string;
  user_id: string;
  label: string;
  role_focus: string | null;
  storage_path: string;
  original_filename: string;
  mime_type: string | null;
  byte_size: number | null;
  is_default: boolean;
  created_at: string;
};

type DocumentsPanelProps = {
  session: Session;
};

const ACCEPTED_EXTENSIONS = new Set(["pdf", "doc", "docx"]);
const SIGNED_URL_TTL_SECONDS = 60;

function extensionOf(filename: string) {
  return filename.split(".").pop()?.toLowerCase() ?? "";
}

function basename(filename: string) {
  return filename.replace(/\.[^.]+$/, "") || "Resume";
}

function sanitizedFilename(filename: string) {
  const extension = extensionOf(filename);
  const stem = basename(filename)
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-zA-Z0-9._-]+/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 120) || "resume";
  return extension ? `${stem}.${extension}` : stem;
}

function formatBytes(value: number | null) {
  if (value == null) return "Unknown size";
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${Math.round(value / 1024)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * Lists and manages the signed-in user's private source resumes. Storage and
 * metadata access are both protected by Supabase RLS; this component never
 * uses a service-role key or assumes access to another user's documents.
 */
export function DocumentsPanel({ session }: DocumentsPanelProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [resumes, setResumes] = useState<Resume[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isUploading, setIsUploading] = useState(false);
  const [busyResumeId, setBusyResumeId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const loadResumes = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const { data, error: queryError } = await requireSupabase()
        .from("resumes")
        .select("id, user_id, label, role_focus, storage_path, original_filename, mime_type, byte_size, is_default, created_at")
        .eq("user_id", session.user.id)
        .order("is_default", { ascending: false })
        .order("created_at", { ascending: false });
      if (queryError) throw queryError;
      setResumes((data ?? []) as Resume[]);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Could not load your resumes.");
    } finally {
      setIsLoading(false);
    }
  }, [session.user.id]);

  useEffect(() => {
    void loadResumes();
  }, [loadResumes]);

  const uploadResume = async (file: File) => {
    const extension = extensionOf(file.name);
    if (!ACCEPTED_EXTENSIONS.has(extension)) {
      setError("Upload a PDF, DOC, or DOCX resume.");
      return;
    }

    setIsUploading(true);
    setError(null);
    setSuccess(null);
    const client = requireSupabase();
    const objectName = `${crypto.randomUUID()}-${sanitizedFilename(file.name)}`;
    const storagePath = `${session.user.id}/${objectName}`;

    try {
      const { error: uploadError } = await client.storage.from("resumes").upload(storagePath, file, {
        contentType: file.type || undefined,
        upsert: false,
      });
      if (uploadError) throw uploadError;

      const { error: insertError } = await client.from("resumes").insert({
        user_id: session.user.id,
        label: basename(file.name),
        storage_path: storagePath,
        original_filename: file.name,
        mime_type: file.type || null,
        byte_size: file.size,
        is_default: resumes.length === 0,
      });
      if (insertError) {
        await client.storage.from("resumes").remove([storagePath]);
        throw insertError;
      }

      setSuccess(`Uploaded ${file.name}.`);
      await loadResumes();
    } catch (uploadError) {
      setError(uploadError instanceof Error ? uploadError.message : "Could not upload your resume.");
    } finally {
      setIsUploading(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  };

  const setDefaultResume = async (resume: Resume) => {
    if (resume.is_default) return;
    setBusyResumeId(resume.id);
    setError(null);
    setSuccess(null);
    const client = requireSupabase();
    try {
      const { error: clearError } = await client
        .from("resumes")
        .update({ is_default: false })
        .eq("user_id", session.user.id)
        .eq("is_default", true);
      if (clearError) throw clearError;

      const { error: defaultError } = await client
        .from("resumes")
        .update({ is_default: true })
        .eq("id", resume.id)
        .eq("user_id", session.user.id);
      if (defaultError) throw defaultError;

      setSuccess(`${resume.label} is now your default resume.`);
      await loadResumes();
    } catch (defaultError) {
      setError(defaultError instanceof Error ? defaultError.message : "Could not set the default resume.");
      await loadResumes();
    } finally {
      setBusyResumeId(null);
    }
  };

  const downloadResume = async (resume: Resume) => {
    setBusyResumeId(resume.id);
    setError(null);
    setSuccess(null);
    try {
      const { data, error: urlError } = await requireSupabase()
        .storage
        .from("resumes")
        .createSignedUrl(resume.storage_path, SIGNED_URL_TTL_SECONDS, { download: resume.original_filename });
      if (urlError) throw urlError;
      window.open(data.signedUrl, "_blank", "noopener,noreferrer");
      setSuccess(`Prepared a private download link for ${resume.original_filename}.`);
    } catch (downloadError) {
      setError(downloadError instanceof Error ? downloadError.message : "Could not create a download link.");
    } finally {
      setBusyResumeId(null);
    }
  };

  const deleteResume = async (resume: Resume) => {
    if (!window.confirm(`Delete ${resume.original_filename}? This cannot be undone.`)) return;
    setBusyResumeId(resume.id);
    setError(null);
    setSuccess(null);
    const client = requireSupabase();
    try {
      const { error: storageError } = await client.storage.from("resumes").remove([resume.storage_path]);
      if (storageError) throw storageError;
      const { error: metadataError } = await client
        .from("resumes")
        .delete()
        .eq("id", resume.id)
        .eq("user_id", session.user.id);
      if (metadataError) throw metadataError;

      setSuccess(`Deleted ${resume.original_filename}.`);
      await loadResumes();
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : "Could not delete your resume.");
    } finally {
      setBusyResumeId(null);
    }
  };

  return (
    <section className="beta-documents-panel" aria-labelledby="documents-title">
      <div className="beta-documents-panel__header">
        <div>
          <p className="beta-eyebrow">Private documents</p>
          <h2 id="documents-title">Your source resumes</h2>
          <p>Only you can access these files. Tailored copies will be kept separately for each application.</p>
        </div>
        <button
          type="button"
          className="beta-button beta-documents-panel__upload"
          onClick={() => inputRef.current?.click()}
          disabled={isUploading}
        >
          {isUploading ? "Uploading…" : "Upload resume"}
        </button>
        <input
          ref={inputRef}
          className="beta-visually-hidden"
          type="file"
          accept=".pdf,.doc,.docx,application/pdf,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) void uploadResume(file);
          }}
          tabIndex={-1}
        />
      </div>

      <div className="beta-documents-panel__feedback" aria-live="polite" aria-atomic="true">
        {error && <p className="beta-message beta-message--error" role="alert">{error}</p>}
        {success && <p className="beta-message beta-message--success">{success}</p>}
      </div>

      {isLoading ? (
        <p className="beta-documents-panel__status">Loading your private resumes…</p>
      ) : resumes.length === 0 ? (
        <div className="beta-documents-panel__empty">
          <p>No resume uploaded yet.</p>
          <p>Upload your approved source resume to use it for tailored applications.</p>
        </div>
      ) : (
        <ul className="beta-documents-list" aria-label="Your resumes">
          {resumes.map((resume) => {
            const isBusy = busyResumeId === resume.id;
            return (
              <li className="beta-documents-list__item" key={resume.id}>
                <div className="beta-documents-list__details">
                  <div className="beta-documents-list__title-row">
                    <strong>{resume.label}</strong>
                    {resume.is_default && <span className="beta-documents-list__default">Default</span>}
                  </div>
                  <p>{resume.original_filename} · {formatBytes(resume.byte_size)}</p>
                </div>
                <div className="beta-documents-list__actions">
                  <button type="button" className="beta-text-button" onClick={() => void downloadResume(resume)} disabled={isBusy}>
                    Download
                  </button>
                  <button
                    type="button"
                    className="beta-text-button"
                    onClick={() => void setDefaultResume(resume)}
                    disabled={isBusy || resume.is_default}
                    aria-label={`Make ${resume.label} the default resume`}
                  >
                    {resume.is_default ? "Default" : "Make default"}
                  </button>
                  <button
                    type="button"
                    className="beta-text-button beta-text-button--danger"
                    onClick={() => void deleteResume(resume)}
                    disabled={isBusy}
                    aria-label={`Delete ${resume.label}`}
                  >
                    Delete
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
