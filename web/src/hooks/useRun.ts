import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";

export function useRunTrigger() {
  const [runId, setRunId] = useState<string | null>(null);
  const queryClient = useQueryClient();

  const fetchMutation = useMutation({
    mutationFn: (url?: string) => api.triggerFetch(url),
    onSuccess: (data) => setRunId(data.run_id),
  });

  const matchMutation = useMutation({
    mutationFn: (limit?: number) => api.triggerMatch(limit),
    onSuccess: (data) => setRunId(data.run_id),
  });

  const runStatus = useQuery({
    queryKey: ["run", runId],
    queryFn: () => api.getRun(runId as string),
    enabled: runId !== null,
    refetchInterval: (query) => {
      const data = query.state.data;
      if (data && data.status !== "running") {
        queryClient.invalidateQueries({ queryKey: ["jobs"] });
        return false;
      }
      return 1500;
    },
  });

  return {
    triggerFetch: fetchMutation.mutate,
    triggerMatch: matchMutation.mutate,
    isTriggering: fetchMutation.isPending || matchMutation.isPending,
    runStatus: runStatus.data,
    clearRun: () => setRunId(null),
  };
}
