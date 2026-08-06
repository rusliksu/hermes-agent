import { useCallback, useEffect, useLayoutEffect, useState } from "react";
import { RefreshCw, ShieldCheck, Users } from "lucide-react";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { Button } from "@nous-research/ui/ui/components/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@nous-research/ui/ui/components/card";
import { H2 } from "@nous-research/ui/ui/components/typography/h2";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { api } from "@/lib/api";
import type { AccessBindingView, AccessUsersResponse } from "@/lib/api";
import { usePageHeader } from "@/contexts/usePageHeader";

function statusTone(row: AccessBindingView): "success" | "warning" | "destructive" {
  if (row.isolation.status === "healthy" && row.active) return "success";
  if (row.isolation.status === "degraded" || row.active) return "warning";
  return "destructive";
}

function AccessTable({ rows }: { rows: AccessBindingView[] }) {
  if (rows.length === 0) {
    return (
      <p className="px-4 py-8 text-center text-sm text-muted-foreground">
        None configured
      </p>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[760px] text-sm">
        <thead className="border-b border-border text-left text-xs uppercase tracking-wider text-muted-foreground">
          <tr>
            <th className="px-4 py-3 font-medium">Principal</th>
            <th className="px-4 py-3 font-medium">Role</th>
            <th className="px-4 py-3 font-medium">Profile</th>
            <th className="px-4 py-3 font-medium">Capabilities</th>
            <th className="px-4 py-3 font-medium">Health</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {rows.map((row) => (
            <tr key={`${row.binding_kind}:${row.principal_id}`}>
              <td className="px-4 py-3 font-mono text-xs">{row.principal_id}</td>
              <td className="px-4 py-3">
                <div className="flex items-center gap-2">
                  <span>{row.role_id}</span>
                  {!row.active && <Badge tone="destructive">inactive</Badge>}
                </div>
              </td>
              <td className="px-4 py-3 font-mono text-xs">{row.profile_id}</td>
              <td className="max-w-[360px] px-4 py-3 text-xs text-muted-foreground">
                {row.effective_capabilities.length
                  ? row.effective_capabilities.join(", ")
                  : "none"}
              </td>
              <td className="px-4 py-3">
                <Badge tone={statusTone(row)}>{row.isolation.status}</Badge>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function AccessUsersPage() {
  const [data, setData] = useState<AccessUsersResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const { setTitle, setEnd } = usePageHeader();

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await api.getAccessUsers());
    } catch (err) {
      setError(String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  useLayoutEffect(() => {
    setTitle("Access / Users");
    setEnd(
      <Button
        type="button"
        ghost
        size="icon"
        onClick={load}
        disabled={loading}
        aria-label="Refresh access registry"
        title="Refresh access registry"
      >
        {loading ? <Spinner /> : <RefreshCw />}
      </Button>,
    );
    return () => {
      setTitle(null);
      setEnd(null);
    };
  }, [load, loading, setEnd, setTitle]);

  if (loading && !data) {
    return (
      <div className="flex items-center justify-center py-24" aria-busy="true">
        <Spinner className="text-2xl text-primary" />
      </div>
    );
  }

  if (error && !data) {
    return (
      <div className="p-4 text-sm text-destructive" role="alert">
        Failed to load access registry: {error}
      </div>
    );
  }

  if (!data) return null;

  return (
    <div className="flex min-w-0 max-w-full flex-col gap-6">
      <Card>
        <CardContent className="flex flex-wrap items-center gap-3 py-4">
          <ShieldCheck className="h-5 w-5 text-muted-foreground" />
          <span className="text-sm">Registry</span>
          <Badge tone={data.enabled ? "success" : "secondary"}>
            {data.enabled ? data.validation.verdict : "not configured"}
          </Badge>
          <span className="text-xs text-muted-foreground">
            Transport identities, delivery targets, filesystem paths and secrets are redacted.
          </span>
        </CardContent>
      </Card>

      {error && (
        <p className="text-sm text-warning" role="status">
          Refresh failed: {error}
        </p>
      )}

      <section className="flex flex-col gap-3" aria-labelledby="access-users-heading">
        <H2 id="access-users-heading" variant="sm" className="flex items-center gap-2 text-muted-foreground">
          <Users className="h-4 w-4" />
          Users ({data.users.length})
        </H2>
        <Card>
          <AccessTable rows={data.users} />
        </Card>
      </section>

      <section className="flex flex-col gap-3" aria-labelledby="access-rooms-heading">
        <H2 id="access-rooms-heading" variant="sm" className="flex items-center gap-2 text-muted-foreground">
          Shared rooms ({data.rooms.length})
        </H2>
        <Card>
          <CardHeader className="sr-only">
            <CardTitle>Shared rooms</CardTitle>
          </CardHeader>
          <AccessTable rows={data.rooms} />
        </Card>
      </section>
    </div>
  );
}
