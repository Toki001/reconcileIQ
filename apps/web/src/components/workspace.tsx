"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { Icon, type IconName } from "./icon";

export const navigation: { href: string; label: string; icon: IconName }[] = [
  { href: "/", label: "Overview", icon: "overview" },
  { href: "/invoices", label: "Invoices", icon: "invoices" },
  { href: "/purchase-orders", label: "Purchase Orders", icon: "orders" },
  { href: "/reconciliation", label: "Reconciliation", icon: "reconciliation" },
  { href: "/exceptions", label: "Exceptions", icon: "exceptions" },
  { href: "/audit-log", label: "Audit Log", icon: "audit" },
];

export function Workspace({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const current =
    navigation.find((item) => item.href === pathname)?.label || "Workspace";
  return (
    <div className="workspace">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <aside className="sidebar">
        <Link href="/" className="brand" aria-label="ReconcileIQ home">
          <span className="brand-mark">
            <Icon name="reconciliation" size={24} />
          </span>
          Reconcile<span className="brand-iq">IQ</span>
        </Link>
        <div className="workspace-switch">
          <span className="workspace-avatar">R</span>
          <span>
            ReconcileIQ Finance<small>Local workspace</small>
          </span>
          <span className="workspace-dot" />
        </div>
        <p className="nav-label">WORKSPACE</p>
        <nav aria-label="Main navigation">
          {navigation.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className={`nav-link ${pathname === item.href ? "active" : ""}`}
              aria-current={pathname === item.href ? "page" : undefined}
            >
              <Icon name={item.icon} />
              <span>{item.label}</span>
            </Link>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="boundary-card">
            <Icon name="shield" />
            <strong>Clarity at every step.</strong>
            <p>
              Deterministic checks.
              <br />
              An audit trail for every record.
            </p>
          </div>
          <div className="profile">
            <span className="profile-avatar">LF</span>
            <span>
              Local finance team<small>Synthetic demo · Read only</small>
            </span>
          </div>
        </div>
      </aside>
      <div className="main-wrap">
        <header className="topbar">
          <div>
            <span className="muted">Workspace</span>
            <span className="breadcrumb-divider">/</span>
            {current}
          </div>
          <div className="topbar-right">
            <span className="environment-badge">
              <span />
              LOCAL DEMO
            </span>
            <span className="topbar-avatar">LF</span>
          </div>
        </header>
        <main id="main" tabIndex={-1}>
          {children}
        </main>
        <footer className="page-footer">
          <span>ReconcileIQ · Milestone 01</span>
          <span>Synthetic records. No payments are executed.</span>
        </footer>
      </div>
    </div>
  );
}
