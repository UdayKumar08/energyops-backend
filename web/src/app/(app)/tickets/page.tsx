import type { Me, Ticket } from "@/api/types";
import { TicketList } from "@/components/TicketList";
import { Notice, PageHeader } from "@/components/ui";
import { backend } from "@/lib/backend";
import { settle } from "@/lib/safe";

export const metadata = { title: "Tickets" };

export default async function TicketsPage() {
  const me = await backend<Me>("/me");
  const tickets = await settle(backend<Ticket[]>("/tickets?limit=100"));
  return (
    <>
      <PageHeader
        title="Tickets"
        lead="Maintenance tickets drafted from unusual events. Nothing is created until a manager approves it. Approved tickets are recorded here only; nothing is sent to another system."
      />
      {tickets.error ? (
        <Notice tone="bad">{tickets.error}</Notice>
      ) : (
        <TicketList tickets={tickets.data ?? []} canApprove={me.permissions.includes("approve_tickets")} />
      )}
    </>
  );
}
