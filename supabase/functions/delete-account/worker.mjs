// The router exposes this worker only when the disposable-project gate is enabled.
export async function processDeletionJob(deps) {
  const job = await deps.claim()
  if (job === null) return { status: 'idle' }
  if (!job || typeof job.job_id !== 'string' || typeof job.user_id !== 'string' ||
      typeof job.lease_id !== 'string') {
    throw new Error('Invalid deletion job')
  }

  try {
    const result = await deps.deleteAtomically(job.job_id, job.lease_id)
    if (result === 'storage_review_required' || result === 'shared_data_review_required') {
      return { status: 'needs_review', job_id: job.job_id }
    }
    if (result !== 'completed') throw new Error('Invalid atomic deletion result')
    return { status: 'completed', job_id: job.job_id }
  } catch (error) {
    deps.logError('Deletion worker failed')
    if (!await deps.finishRetry(job.job_id, job.lease_id)) {
      deps.logError('Deletion job lease lost or already completed; retry will use the current job state')
    }
    return { status: 'retry', job_id: job.job_id }
  }
}

export function deletionWorkerDependencies(admin, logError, { enableClaim = false } = {}) {
  async function rpc(name, args) {
    const { data, error } = await admin.rpc(name, args)
    if (error) throw new Error(`Deletion RPC ${name} failed`)
    return data
  }
  return {
    claim: () => {
      if (!enableClaim) {
        throw new Error('Deletion worker disabled: atomic deletion is not verified on Supabase')
      }
      return rpc('falconbroom_claim_deletion_job', {})
    },
    deleteAtomically: (jobId, leaseId) => rpc('falconbroom_atomic_no_storage_deletion', {
      p_job_id: jobId, p_lease_id: leaseId,
    }),
    finishRetry: async (jobId, leaseId) => {
      const data = await rpc('falconbroom_finish_deletion_job', {
        p_job_id: jobId, p_lease_id: leaseId, p_result: 'retry',
      })
      if (typeof data !== 'boolean') throw new Error('Invalid deletion job result')
      return data
    },
    logError,
  }
}
