"use client";

import { useState } from "react";
import type { Band, Tariff, TariffBody } from "@/api/types";
import { call, messageOf } from "@/lib/client";
import { Field, Notice } from "./ui";

const empty: Band = { name: "", start_hour: 0, end_hour: 24, price_per_kwh: 0, days: "all" };

export function TariffForm({ site, initial }: { site: string; initial: Tariff | null }) {
  const [label, setLabel] = useState(initial?.label ?? "");
  const [currency, setCurrency] = useState<TariffBody["currency"]>(initial?.currency ?? "GBP");
  const [price, setPrice] = useState(String(initial?.price_per_kwh ?? ""));
  const [demand, setDemand] = useState(String(initial?.demand_charge_per_kw_month ?? 0));
  const [bands, setBands] = useState<Band[]>(initial?.bands ?? []);
  const [message, setMessage] = useState<{ tone: "ok" | "bad"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  function change(i: number, patch: Partial<Band>) {
    setBands((b) => b.map((x, n) => (n === i ? { ...x, ...patch } : x)));
  }

  async function save(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setMessage(null);
    try {
      await call<Tariff>("PUT", `/sites/${encodeURIComponent(site)}/tariff`, {
        body: {
          label,
          currency,
          price_per_kwh: Number(price),
          demand_charge_per_kw_month: Number(demand || 0),
          bands: bands.map((b) => ({ ...b, price_per_kwh: Number(b.price_per_kwh) })),
        } satisfies TariffBody,
      });
      setMessage({ tone: "ok", text: "Tariff saved." });
    } catch (e) {
      setMessage({ tone: "bad", text: messageOf(e) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={save} className="max-w-3xl">
      {initial?.is_demo ? (
        <div className="mb-4">
          <Notice tone="warn">This tariff is invented for the demo. Replace it with your supplier&apos;s real prices.</Notice>
        </div>
      ) : null}
      <div className="grid gap-x-6 sm:grid-cols-2">
        <Field label="Name of the tariff" htmlFor="label" hint="For example the supplier and contract.">
          <input id="label" className="field" value={label} maxLength={200} required onChange={(e) => setLabel(e.target.value)} />
        </Field>
        <Field label="Currency" htmlFor="currency">
          <select id="currency" className="field" value={currency} onChange={(e) => setCurrency(e.target.value as TariffBody["currency"])}>
            <option value="GBP">Pounds (GBP)</option>
            <option value="EUR">Euros (EUR)</option>
            <option value="USD">US dollars (USD)</option>
          </select>
        </Field>
        <Field label="Standard price per kWh" htmlFor="price" hint="Used for any hour no price band covers.">
          <input id="price" className="field" inputMode="decimal" required value={price} onChange={(e) => setPrice(e.target.value)} />
        </Field>
        <Field label="Demand charge per kW per month" htmlFor="demand" hint="Leave at 0 if the contract has none.">
          <input id="demand" className="field" inputMode="decimal" value={demand} onChange={(e) => setDemand(e.target.value)} />
        </Field>
      </div>

      <h3 className="mb-2 mt-4 font-semibold">Price bands</h3>
      {bands.length === 0 ? <p className="mb-2 text-sm text-muted">No bands. The standard price applies at all times.</p> : null}
      {bands.map((b, i) => (
        <fieldset key={i} className="mb-3 grid gap-3 border border-line p-3 sm:grid-cols-6">
          <legend className="px-1 text-sm font-semibold">Band {i + 1}</legend>
          <div className="sm:col-span-2">
            <label className="label" htmlFor={`bn-${i}`}>Name</label>
            <input id={`bn-${i}`} className="field" value={b.name} maxLength={40} onChange={(e) => change(i, { name: e.target.value })} />
          </div>
          <div>
            <label className="label" htmlFor={`bs-${i}`}>From hour</label>
            <input id={`bs-${i}`} className="field" type="number" min={0} max={23} value={b.start_hour} onChange={(e) => change(i, { start_hour: Number(e.target.value) })} />
          </div>
          <div>
            <label className="label" htmlFor={`be-${i}`}>To hour</label>
            <input id={`be-${i}`} className="field" type="number" min={1} max={24} value={b.end_hour} onChange={(e) => change(i, { end_hour: Number(e.target.value) })} />
          </div>
          <div>
            <label className="label" htmlFor={`bp-${i}`}>Price per kWh</label>
            <input id={`bp-${i}`} className="field" inputMode="decimal" value={b.price_per_kwh} onChange={(e) => change(i, { price_per_kwh: e.target.value as unknown as number })} />
          </div>
          <div>
            <label className="label" htmlFor={`bd-${i}`}>Days</label>
            <select id={`bd-${i}`} className="field" value={b.days ?? "all"} onChange={(e) => change(i, { days: e.target.value as Band["days"] })}>
              <option value="all">Every day</option>
              <option value="weekday">Weekdays</option>
              <option value="weekend">Weekends</option>
            </select>
          </div>
          <div className="sm:col-span-6">
            <button type="button" className="btn btn-small btn-danger" onClick={() => setBands((x) => x.filter((_, n) => n !== i))}>
              Remove band {i + 1}
            </button>
          </div>
        </fieldset>
      ))}
      <button type="button" className="btn btn-small" disabled={bands.length >= 12} onClick={() => setBands((b) => [...b, { ...empty }])}>
        Add a band
      </button>

      <div className="mt-6 flex items-center gap-4">
        <button className="btn btn-primary" type="submit" disabled={busy}>
          {busy ? "Saving" : "Save tariff"}
        </button>
        <div aria-live="polite">{message ? <Notice tone={message.tone}>{message.text}</Notice> : null}</div>
      </div>
    </form>
  );
}
