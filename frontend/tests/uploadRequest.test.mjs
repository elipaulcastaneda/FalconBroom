import test from 'node:test'
import assert from 'node:assert/strict'
import { File } from 'node:buffer'
import { uploadRequest } from '../src/uploadRequest.mjs'

test('local desktop uploads stream the original file with an encoded filename', () => {
  const file = new File(['private'], 'café.csv', {type: 'text/csv'})
  const request = uploadRequest(file, true)
  assert.equal(request.path, '/desktop/upload-stream')
  assert.equal(request.options.body, file)
  assert.equal(request.options.headers['X-FalconBroom-Filename'], 'caf%C3%A9.csv')
  assert.equal(request.options.headers['Content-Type'], 'application/octet-stream')
})

test('remote and browser uploads retain multipart behavior', () => {
  const file = new File(['private'], 'example.csv', {type: 'text/csv'})
  const request = uploadRequest(file, false)
  assert.equal(request.path, '/upload')
  const uploaded = request.options.body.get('file')
  assert.equal(uploaded.name, file.name)
  assert.equal(uploaded.size, file.size)
})
