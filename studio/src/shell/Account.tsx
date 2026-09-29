// The header's account chip (cloud plan 5) and the access banner (4.4). Billing and machines live on
// spriteplay.com: every such action opens the website.

import { host } from '../host';
import { BILLING, useCloud, useGenerationLock } from '../lib/cloud';
import { fileSize } from '../lib/format';
import type { CloudAccess, CloudStatus } from '../lib/types';
import { Button, Menu, type MenuEntry } from '../ui/controls';
import { Icon } from '../ui/Icon';

function initials(s: CloudStatus): string {
  const src = (s.user?.name || s.user?.email || '?').trim();
  const parts = src.split(/[\s@._-]+/).filter(Boolean);
  return ((parts[0]?.[0] ?? '?') + (parts.length > 1 ? parts[1][0] : '')).toUpperCase();
}

/** "Trial, 5 days", "License", "Pro", "Team" or "Locked". */
export function accessLabel(a: CloudAccess, s?: CloudStatus | null): string {
  if (!a.allowed) return a.reason === 'offline' ? 'Offline' : 'Locked';
  if (a.status === 'trial') {
    const d = a.trial_days_left ?? 0;
    return d === 0 ? 'Trial, last day' : d === 1 ? 'Trial, 1 day' : `Trial, ${d} days`;
  }
  if (a.status === 'license') return 'License';
  if (a.status === 'team') return 'Team';
  if (a.status === 'pro') return s?.personal?.via === 'team' ? 'Pro, through a team' : 'Pro';
  return a.status;
}

const PLAN_LABEL: Record<string, string> = { free: 'Free', license: 'License', pro: 'Pro' };

function Meter({ used, limit, testid }: { used: number; limit: number | null; testid?: string }) {
  const pct = limit ? Math.min(100, (used / limit) * 100) : 0;
  return (
    <span className="account-meter" data-testid={testid}>
      <span className="account-meter-bar" aria-hidden="true">
        <span style={{ width: `${pct}%` }} className={pct > 90 ? 'hot' : ''} />
      </span>
      <span className="account-meter-text">
        {fileSize(used)} of {limit ? fileSize(limit) : 'unlimited'}
      </span>
    </span>
  );
}

function AccountSummary({ s }: { s: CloudStatus }) {
  const via = s.personal?.via === 'team' ? s.teams.find((t) => t.active)?.name : null;
  return (
    <div className="account-summary">
      <div className="account-who">
        <span className="account-name" data-testid="account-name">{s.user?.name || s.user?.email}</span>
        <span className="account-email" data-testid="account-email">{s.user?.email}</span>
      </div>
      <div className="account-row" data-testid="account-plan" data-plan={s.personal?.plan}>
        <span>Personal</span>
        <span className="account-plan">
          {s.personal ? (via ? `Pro, through ${via}` : PLAN_LABEL[s.personal.plan] ?? s.personal.plan) : '…'}
          {' · '}
          {accessLabel(s.access, s)}
        </span>
      </div>
      {s.personal ? <Meter used={s.personal.storage.used} limit={s.personal.storage.limit} testid="account-storage" /> : null}
      {s.teams.map((t) => (
        <div key={t.id} className="account-row" data-testid="account-team" data-active={String(t.active)}>
          <span>{t.name}</span>
          <span className={t.active ? 'account-plan' : 'account-plan is-off'}>{t.active ? `Team · ${t.role}` : 'Team plan needed'}</span>
        </div>
      ))}
      {s.account_error ? <div className="account-note">{s.account_error.message}</div> : null}
      {!s.persisted ? <div className="account-note">This machine has no keychain, so you stay signed in only until SpritePlay closes.</div> : null}
      <div className="account-note">This machine: {s.machine.name}</div>
    </div>
  );
}

export function AccountChip() {
  const cloud = useCloud();
  const s = cloud.status;
  if (!s) return null;
  if (!s.signed_in) {
    if (s.pending_sign_in) {
      return (
        <Menu
          label="Signing in"
          testid="account-pending"
          className="account-chip is-pending"
          trigger={
            <>
              <Icon name="refresh" size={14} />
              <span>Finish in your browser</span>
            </>
          }
          header="Approve this machine on spriteplay.com, then come back here."
          entries={[
            { key: 'reopen', label: 'Open the browser again', testid: 'account-reopen', onSelect: cloud.reopenSignIn },
            { key: 'cancel', label: 'Cancel sign-in', testid: 'account-cancel', onSelect: () => void cloud.cancelSignIn() },
          ]}
        />
      );
    }
    return (
      <Button size="sm" variant="primary" busy={cloud.busy} onClick={() => void cloud.signIn()} data-testid="account-sign-in">
        Sign in
      </Button>
    );
  }
  const a = s.access;
  const entries: MenuEntry[] = [];
  if (!a.allowed && a.reason !== 'offline') {
    entries.push(
      { key: 'buy', label: 'Buy the License', testid: 'account-buy', onSelect: () => cloud.openSite(BILLING.license) },
      { key: 'plans', label: 'See plans', testid: 'account-plans', onSelect: () => cloud.openSite(BILLING.plans) },
    );
  }
  if (!a.allowed) entries.push({ key: 'check', label: 'Check again', testid: 'account-check', onSelect: () => void cloud.checkAgain() });
  const up = s.update;
  if (up?.available && up.url) {
    entries.push({ key: 'update', label: `Update available: SpritePlay ${up.version}`, testid: 'account-update', onSelect: () => void host.openExternal(up.url!) });
  }
  entries.push(
    { key: 'manage', label: 'Manage account', divider: entries.length > 0, testid: 'account-manage', onSelect: () => cloud.openSite(BILLING.account) },
    { key: 'machines', label: 'Machines', testid: 'account-machines', onSelect: () => cloud.openSite(BILLING.machines) },
    { key: 'out', label: 'Sign out', divider: true, testid: 'account-sign-out', onSelect: () => void cloud.signOut() },
  );
  return (
    <Menu
      label="Account"
      testid="account-chip"
      menuTestid="account-menu"
      className={`account-chip${a.allowed ? '' : ' is-locked'}`}
      align="end"
      data={{ status: a.status, allowed: String(a.allowed), reason: a.reason }}
      title={s.user?.email ?? undefined}
      header={<AccountSummary s={s} />}
      entries={entries}
      trigger={
        <>
          <span className="account-avatar" aria-hidden="true">{initials(s)}</span>
          <span className="account-access" data-testid="account-access">{accessLabel(a, s)}</span>
        </>
      }
    />
  );
}

/** Why sprites can't be made, and what to do (cloud plan 4.4). `always` shows it with no project
 * (the gallery); otherwise it appears only when the open project's mode needs access. */
export function AccessBanner({ always = false, testid = 'access-banner' }: { always?: boolean; testid?: string }) {
  const cloud = useCloud();
  const { locked, status: s } = useGenerationLock();
  if (!s) return null;
  const a = s.access;
  const ending = a.allowed && a.status === 'trial' && (a.trial_days_left ?? 99) <= 2;
  if (!ending && !(always ? !a.allowed : locked)) return null;
  const buy = (
    <>
      <Button size="sm" variant="primary" onClick={() => cloud.openSite(BILLING.license)} data-testid={`${testid}-buy`}>
        Buy the License
      </Button>
      <Button size="sm" onClick={() => cloud.openSite(BILLING.plans)} data-testid={`${testid}-plans`}>
        See plans
      </Button>
    </>
  );
  let text = a.message;
  let actions = null;
  if (ending) {
    const d = a.trial_days_left ?? 0;
    text = d === 0 ? 'Trial: ends today.' : d === 1 ? 'Trial: 1 day left.' : `Trial: ${d} days left.`;
    actions = buy;
  } else if (a.reason === 'signed_out') {
    if (s.pending_sign_in) {
      text = 'Finish signing in in your browser, then come back here.';
      actions = (
        <Button size="sm" onClick={cloud.reopenSignIn} data-testid={`${testid}-reopen`}>
          Open the browser again
        </Button>
      );
    } else {
      actions = (
        <Button size="sm" variant="primary" busy={cloud.busy} onClick={() => void cloud.signIn()} data-testid={`${testid}-sign-in`}>
          Sign in
        </Button>
      );
    }
  } else if (a.reason === 'version') {
    const up = s.update;
    actions = (
      <>
        <Button size="sm" variant="primary" onClick={() => cloud.openSite(BILLING.license)} data-testid={`${testid}-renew`}>
          Renew updates
        </Button>
        {up?.url && up.version ? (
          <Button size="sm" onClick={() => void host.openExternal(up.url!)} data-testid={`${testid}-download`}>
            Download {up.version}
          </Button>
        ) : null}
        <Button size="sm" onClick={() => void cloud.checkAgain()} busy={cloud.busy} data-testid={`${testid}-check`}>
          Check again
        </Button>
      </>
    );
  } else if (a.reason === 'offline') {
    actions = (
      <Button size="sm" onClick={() => void cloud.checkAgain()} busy={cloud.busy} data-testid={`${testid}-check`}>
        Check again
      </Button>
    );
  } else {
    actions = (
      <>
        {a.status === 'trial_ended' ? buy : null}
        <Button size="sm" variant="quiet" onClick={() => void cloud.checkAgain()} busy={cloud.busy} data-testid={`${testid}-check`}>
          I’ve bought it: check again
        </Button>
      </>
    );
  }
  return (
    <div
      className={`access-banner${ending ? ' is-info' : ''}`}
      role="status"
      data-testid={testid}
      data-reason={ending ? 'trial_ending' : a.reason}
      data-status={a.status}
    >
      <Icon name={ending ? 'sparkle' : 'stop'} size={16} />
      <span className="access-banner-text" data-testid={`${testid}-text`}>{text}</span>
      <span className="access-banner-actions">{actions}</span>
    </div>
  );
}

/** "SpritePlay <version> is available" in the gallery (cloud plan 9). */
export function UpdateBanner() {
  const cloud = useCloud();
  const up = cloud.status?.update;
  if (!up || (!up.available && !up.needs_renewal)) return null;
  return (
    <div className="access-banner is-info" role="status" data-testid="update-banner">
      <Icon name="download" size={16} />
      <span className="access-banner-text" data-testid="update-banner-text">
        {up.available ? `SpritePlay ${up.version} is available.` : ''}
        {up.needs_renewal ? `${up.available ? ' ' : ''}SpritePlay ${up.latest} needs renewed updates.` : ''}
      </span>
      <span className="access-banner-actions">
        {up.available && up.url ? (
          <Button size="sm" variant="primary" onClick={() => void host.openExternal(up.url!)} data-testid="update-download">
            Download
          </Button>
        ) : null}
        {up.needs_renewal ? (
          <Button size="sm" onClick={() => cloud.openSite(BILLING.license)} data-testid="update-renew">
            Renew updates
          </Button>
        ) : null}
      </span>
    </div>
  );
}
