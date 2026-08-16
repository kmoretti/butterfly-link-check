import yaml from 'js-yaml'

export function parseLinkYml(content) {
  const groups = yaml.load(content) || []
  if (!Array.isArray(groups)) throw new Error('YAML 顶层必须是友链分组数组')

  for (const [groupIndex, group] of groups.entries()) {
    if (!group || typeof group !== 'object' || Array.isArray(group)) {
      throw new Error(`第 ${groupIndex + 1} 个分组必须是对象`)
    }
    if (!Array.isArray(group.link_list)) {
      throw new Error(`分组“${group.class_name || groupIndex + 1}”缺少 link_list 数组`)
    }
    for (const [linkIndex, link] of group.link_list.entries()) {
      if (!link || typeof link !== 'object' || Array.isArray(link)) {
        throw new Error(`分组“${group.class_name || groupIndex + 1}”第 ${linkIndex + 1} 条友链必须是对象`)
      }
    }
  }

  return groups
}

export function serializeLinkYml(groups) {
  return yaml.dump(groups, { indent: 2, lineWidth: -1, noRefs: true })
}

export function normalizeTags(value) {
  const values = Array.isArray(value) ? value : typeof value === 'string' ? value.split(',') : []
  return [...new Set(values.filter(item => typeof item === 'string').map(item => item.trim()).filter(Boolean))]
}

export function getScreenshotValue(link) {
  if (typeof link.siteshot === 'string' && link.siteshot.trim()) return link.siteshot
  if (typeof link.topimg === 'string' && link.topimg.trim()) return link.topimg
  return ''
}

export function updateOptionalField(link, key, value) {
  const updated = { ...link }
  if (typeof value === 'string' && value.trim()) updated[key] = value.trim()
  else delete updated[key]
  return updated
}

export function updateTags(link, value) {
  const updated = { ...link }
  const tags = normalizeTags(value)
  if (tags.length) updated.tags = tags
  else delete updated.tags
  return updated
}

export function updateScreenshot(link, value, groups) {
  const updated = { ...link }
  const screenshot = typeof value === 'string' ? value.trim() : ''

  if (!screenshot) {
    delete updated.siteshot
    delete updated.topimg
    return updated
  }

  if (Object.prototype.hasOwnProperty.call(link, 'siteshot') && Object.prototype.hasOwnProperty.call(link, 'topimg')) {
    updated.siteshot = screenshot
    updated.topimg = screenshot
  } else if (Object.prototype.hasOwnProperty.call(link, 'topimg')) {
    updated.topimg = screenshot
  } else if (Object.prototype.hasOwnProperty.call(link, 'siteshot')) {
    updated.siteshot = screenshot
  } else if (usesTopimg(groups)) {
    updated.topimg = screenshot
  } else {
    updated.siteshot = screenshot
  }

  return updated
}

function usesTopimg(groups) {
  return groups.some(group => group.link_list.some(link => Object.prototype.hasOwnProperty.call(link, 'topimg') && !Object.prototype.hasOwnProperty.call(link, 'siteshot')))
}
