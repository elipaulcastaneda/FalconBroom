export async function fetchEncryptedZip(controlUrl, payload, password, request = fetch) {
  const response = await request(`${controlUrl}/desktop/encrypted-download`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    credentials: 'omit',
    cache: 'no-store',
    body: JSON.stringify({...payload, password}),
  })
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    throw new Error(body?.detail || `Encrypted download failed (${response.status})`)
  }
  return response.blob()
}
