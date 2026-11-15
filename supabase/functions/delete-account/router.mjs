import { handleDeletionPreflight, supabaseDependencies } from './core.mjs'
import { deletionJobDependencies, handleDeletionJobRequest } from './jobs.mjs'
import { deletionWorkerDependencies } from './worker.mjs'
import { handleDeletionWorkerRequest } from './worker-http.mjs'

const TEST_PROJECT_URL = 'https://zthtkoekvxplppxanwiu.supabase.co'

export function deletionRouter({ config, enableTestDeletion, workerSecret, createClient, logError }) {
  const enabled = enableTestDeletion === '1' && config.url === TEST_PROJECT_URL &&
    typeof workerSecret === 'string' && workerSecret.length >= 32 && workerSecret.length <= 512
  const authDeps = supabaseDependencies(createClient, config.url, config.anonKey, config.serviceKey, logError)
  let jobs
  let worker
  if (enabled) {
    const admin = createClient(config.url, config.serviceKey, {
      auth: { persistSession: false, autoRefreshToken: false, detectSessionInUrl: false },
    })
    jobs = deletionJobDependencies(authDeps, admin)
    worker = deletionWorkerDependencies(admin, logError, { enableClaim: true })
  }

  return request => {
    if (new URL(request.url).pathname.endsWith('/worker')) {
      return handleDeletionWorkerRequest(request, { enabled, secret: workerSecret, worker, logError })
    }
    return enabled
      ? handleDeletionJobRequest(request, jobs)
      : handleDeletionPreflight(request, authDeps)
  }
}
