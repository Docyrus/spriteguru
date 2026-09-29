import { useState } from 'react';
import { api } from '../lib/api';
import { useEngineEvent } from '../lib/events';
import { usd } from '../lib/format';
import { errText, useStore } from '../lib/store';
import { Button, Modal } from '../ui/controls';
import type { JobState } from '../lib/types';

/** Budget approvals: a job over its cap waits until you approve or deny the next paid call. */
export function Approvals() {
  const { jobs, reloadJobs, toast } = useStore();
  const [busy, setBusy] = useState<'yes' | 'no' | null>(null);
  const [handled, setHandled] = useState<Record<string, string>>({});

  useEngineEvent((e) => {
    if (e.type === 'approval_required') void reloadJobs();
  });

  const pending = (jobs ?? []).filter(
    (j): j is JobState & { approval: NonNullable<JobState['approval']> } =>
      j.state === 'awaiting_approval' && !!j.approval && handled[j.id] !== j.updated,
  );
  const job = pending[0];
  if (!job) return null;
  const a = job.approval;

  const answer = async (ok: boolean) => {
    setBusy(ok ? 'yes' : 'no');
    try {
      const res = await api.approveJob(job.id, ok);
      setHandled((h) => ({ ...h, [job.id]: job.updated }));
      if (!res.delivered) toast('That approval request had already closed.', 'info');
      else toast(ok ? 'Approved; the job continues.' : 'Denied; the job stops before the call.', ok ? 'ok' : 'info');
      void reloadJobs();
    } catch (e) {
      toast(errText(e), 'error');
    } finally {
      setBusy(null);
    }
  };

  return (
    <Modal
      title="Approve spending?"
      testid="approval-modal"
      actions={
        <>
          <Button variant="ghost" onClick={() => void answer(false)} busy={busy === 'no'} disabled={busy !== null} data-testid="approval-deny">
            Deny
          </Button>
          <Button variant="primary" onClick={() => void answer(true)} busy={busy === 'yes'} disabled={busy !== null} data-testid="approval-approve">
            Approve {usd(a.estimate)}
          </Button>
        </>
      }
    >
      <p className="modal-lede">
        <strong>{job.anim_id ?? job.id}</strong> wants to make a call that goes over a budget cap.
      </p>
      <dl className="kv" data-testid="approval-details">
        <dt>Model</dt>
        <dd data-testid="approval-model">{a.model}</dd>
        <dt>Operation</dt>
        <dd>{a.op}</dd>
        <dt>Estimate</dt>
        <dd className="num" data-testid="approval-estimate">{usd(a.estimate)}</dd>
        <dt>Reason</dt>
        <dd data-testid="approval-reason">{a.reason}</dd>
        <dt>Spent this job</dt>
        <dd className="num">{usd(a.job_spend)}</dd>
        <dt>Spent this session</dt>
        <dd className="num">{usd(a.session_spend)}</dd>
      </dl>
      {pending.length > 1 ? <p className="muted">{pending.length - 1} more waiting after this one.</p> : null}
    </Modal>
  );
}
