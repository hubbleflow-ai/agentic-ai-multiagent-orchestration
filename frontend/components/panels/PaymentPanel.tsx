"use client";

import { useSelector } from "react-redux";
import type { RootState } from "@/store";
import type { PaymentStatus } from "@/store/tripSlice";
import { CardIcon, CheckIcon } from "../icons";

const LABELS: Record<PaymentStatus, string> = {
  idle: "—",
  authorized: "Authorized · awaiting capture",
  captured: "Captured · trip booked",
  declined: "Declined",
};

export function PaymentPanel() {
  const payment = useSelector((s: RootState) => s.trip.payment);
  if (payment.status === "idle") return null;

  const tone =
    payment.status === "captured"
      ? "border-success/40 bg-success/5 text-success"
      : payment.status === "declined"
      ? "border-danger/40 bg-danger/5 text-danger"
      : "border-accent/40 bg-accentSoft text-accent";

  return (
    <section className="space-y-2">
      <header className="flex items-center gap-2 px-1 text-[12px] uppercase tracking-wider text-muted">
        <CardIcon className="h-[14px] w-[14px]" />
        <span>Payment</span>
      </header>
      <div className={`rounded-xl border px-4 py-3 ${tone}`}>
        <div className="flex items-baseline justify-between">
          <span className="font-serif text-[14px] font-medium">
            {LABELS[payment.status]}
          </span>
          {payment.status === "captured" && <CheckIcon className="h-4 w-4" />}
        </div>
        {payment.amount_inr !== null && (
          <div className="mt-1 font-mono text-[15px] text-ink">
            ₹{(payment.amount_inr / 100000).toFixed(2)} lakh
          </div>
        )}
        {payment.auth_id && (
          <div className="mt-1 font-mono text-[10.5px] text-muted">
            auth · {payment.auth_id}
          </div>
        )}
        {payment.transaction_id && (
          <div className="mt-0.5 font-mono text-[10.5px] text-muted">
            txn · {payment.transaction_id}
          </div>
        )}
      </div>
    </section>
  );
}
