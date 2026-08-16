import assert from 'node:assert/strict'
import test from 'node:test'
import {
  normalizeTags,
  parseLinkYml,
  updateOptionalField,
  updateScreenshot,
  updateTags,
} from '../lib/link-yaml.mjs'

test('parseLinkYml rejects unexpected structures', () => {
  assert.throws(() => parseLinkYml('class_name: invalid'), /顶层/)
  assert.throws(() => parseLinkYml('- class_name: Missing list'), /link_list/)
})

test('updates preserve unknown fields and normalize optional values', () => {
  const link = {
    name: 'Example',
    link: 'https://example.com',
    custom: { preserved: true },
    tags: ['技术', ' 技术 ', '', '博客'],
  }
  let updated = updateOptionalField(link, 'friendslink', ' https://example.com/links ')
  updated = updateTags(updated, updated.tags)

  assert.deepEqual(updated.custom, { preserved: true })
  assert.equal(updated.friendslink, 'https://example.com/links')
  assert.deepEqual(updated.tags, ['技术', '博客'])
  assert.deepEqual(normalizeTags('技术, 技术, 博客, '), ['技术', '博客'])
  assert.equal('tags' in updateTags(updated, ''), false)
  assert.equal('friendslink' in updateOptionalField(updated, 'friendslink', '  '), false)
})

test('screenshot updates retain the established source key', () => {
  const groups = [{ link_list: [] }]
  assert.deepEqual(updateScreenshot({ topimg: 'old' }, 'new', groups), { topimg: 'new' })
  assert.deepEqual(updateScreenshot({ siteshot: 'old' }, 'new', groups), { siteshot: 'new' })
  assert.deepEqual(updateScreenshot({ topimg: 'old', siteshot: 'old' }, 'new', groups), { topimg: 'new', siteshot: 'new' })
  assert.deepEqual(updateScreenshot({}, 'new', [{ link_list: [{ topimg: 'old' }] }]), { topimg: 'new' })
  assert.deepEqual(updateScreenshot({}, 'new', groups), { siteshot: 'new' })
})
