export function effectState(summary) {
  const e = summary.effects;
  const counts = [e.approval_decisions, e.approved_suppliers, e.synthetic_notifications];
  if (counts.some(n => !Number.isInteger(n) || n < 0)) throw new Error('Invalid effect evidence');
  if (counts.some(n => n > 1)) return 'Duplicate effect detected';
  if (counts.every(n => n === 1)) return 'Effects committed';
  if (summary.business_status === 'rejected') return 'Supplier rejected';
  if (summary.business_status === 'cancelled') return 'Case cancelled';
  if (summary.business_status === 'failed') return 'Review failed';
  return 'Awaiting decision';
}

export function executionNote(summary) {
  if (effectState(summary) === 'Duplicate effect detected')
    return 'Do not treat this case as recovered. Inspect the duplicated persisted effects.';
  if (!summary.temporal_observed) return 'Temporal observation unavailable. Business evidence is not proof of execution completion.';
  if (summary.temporal_status === 'COMPLETED' && effectState(summary) === 'Effects committed')
    return 'Temporal has completed. The three persisted business effects remain one each.';
  if (summary.temporal_status === 'COMPLETED' && summary.business_status === 'rejected')
    return 'Human rejection recorded. No approved supplier record or notification was created.';
  if (summary.temporal_status === 'RUNNING' && effectState(summary) === 'Effects committed')
    return 'Effects are saved, but Temporal has not completed. Redelivery must preserve the same outcome.';
  return 'A human decision is required. The model has no approval authority.';
}
