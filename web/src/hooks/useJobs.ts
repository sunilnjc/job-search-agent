import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { Status } from "../types";

export function useJobs() {
  return useQuery({ queryKey: ["jobs"], queryFn: api.listJobs, refetchInterval: 15000 });
}

export function useJob(id: number | null) {
  return useQuery({
    queryKey: ["job", id],
    queryFn: () => api.getJob(id as number),
    enabled: id !== null,
  });
}

export function useUpdateStatus() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, status }: { id: number; status: Status }) => api.updateStatus(id, status),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["jobs"] }),
  });
}

export function useGenerateDraft() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.generateDraft(id),
    onSuccess: (_data, id) => {
      queryClient.invalidateQueries({ queryKey: ["job", id] });
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
    },
  });
}

export function useGenerateGaps() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.generateGaps(id),
    onSuccess: (_data, id) => queryClient.invalidateQueries({ queryKey: ["job", id] }),
  });
}

export function useExcludeJob() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, reason }: { id: number; reason: string }) => api.excludeJob(id, reason),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["jobs"] }),
  });
}

export function useUnexcludeJob() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.unexcludeJob(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["jobs"] }),
  });
}
