export function bindAccountFetch(fetcher, accountId) {
  return (url, opts = {}) => {
    const headers = { ...(opts.headers || {}) }
    if (accountId) headers['X-FalconBroom-Expected-Account'] = accountId.toLowerCase()
    return fetcher(url, { ...opts, headers })
  }
}
