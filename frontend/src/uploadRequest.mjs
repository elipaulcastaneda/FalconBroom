export function uploadRequest(file, localDesktop) {
  if (localDesktop) {
    return {
      path: '/desktop/upload-stream',
      options: {
        method: 'POST',
        headers: {
          'Content-Type': 'application/octet-stream',
          'X-FalconBroom-Filename': encodeURIComponent(file.name),
        },
        body: file,
      },
    }
  }
  const form = new FormData()
  form.append('file', file)
  return { path: '/upload', options: { method: 'POST', body: form } }
}
