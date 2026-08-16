import { useState, useRef, useCallback } from 'react'
import {
  getScreenshotValue,
  normalizeTags,
  parseLinkYml,
  serializeLinkYml,
  updateOptionalField,
  updateScreenshot,
  updateTags,
} from '../lib/link-yaml.mjs'

const FALLBACK_AVATAR = 'data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect fill="%23e2e8f0" width="64" height="64"/><text x="32" y="40" text-anchor="middle" fill="%23999" font-size="24">?</text></svg>'

export default function LinkSorter({ content, onChange, onSave, isLoading }) {
  const dragRef = useRef(null)
  const [dragState, setDragState] = useState(null)
  const [hoverZone, setHoverZone] = useState(null)
  const [collapsed, setCollapsed] = useState({})
  const [expanded, setExpanded] = useState({})

  let data = []
  let parseError = null
  try {
    data = parseLinkYml(content)
  } catch (error) {
    parseError = error instanceof Error ? error.message : '无法解析 YAML'
  }

  const commit = useCallback((groups) => {
    onChange(serializeLinkYml(groups))
  }, [onChange])

  const cloneGroups = () => data.map(group => ({ ...group, link_list: [...group.link_list] }))
  const getZoneKey = (sectionIndex, linkIndex) => `${sectionIndex}-${linkIndex}`
  const getLinkKey = (sectionIndex, linkIndex) => `link-${sectionIndex}-${linkIndex}`

  const handlePointerDown = useCallback((event, sectionIndex, linkIndex) => {
    if (event.target.closest('input, button, select, textarea, summary')) return
    event.preventDefault()
    event.currentTarget.setPointerCapture(event.pointerId)
    dragRef.current = { sectionIndex, linkIndex }
    setDragState({ sectionIndex, linkIndex })
  }, [])

  const handlePointerMove = useCallback((event) => {
    if (!dragRef.current) return
    event.preventDefault()
    document.body.style.userSelect = 'none'
    document.body.style.webkitUserSelect = 'none'
    const target = document.elementFromPoint(event.clientX, event.clientY)
    const insertZone = target?.closest('[data-insert-zone]')
    setHoverZone(insertZone ? insertZone.dataset.insertZone : null)
  }, [])

  const handlePointerUp = useCallback((event) => {
    if (!dragRef.current) return
    event.preventDefault()
    const source = dragRef.current
    dragRef.current = null
    setDragState(null)
    setHoverZone(null)
    document.body.style.userSelect = ''
    document.body.style.webkitUserSelect = ''

    const target = document.elementFromPoint(event.clientX, event.clientY)
    const insertZone = target?.closest('[data-insert-zone]')
    if (!insertZone) return

    const [targetSectionIndex, targetLinkIndex] = insertZone.dataset.insertZone.split('-').map(Number)
    if (source.sectionIndex === targetSectionIndex && (source.linkIndex === targetLinkIndex || source.linkIndex + 1 === targetLinkIndex)) return

    const groups = cloneGroups()
    const [moved] = groups[source.sectionIndex].link_list.splice(source.linkIndex, 1)
    const insertIndex = source.sectionIndex === targetSectionIndex && source.linkIndex < targetLinkIndex
      ? targetLinkIndex - 1
      : targetLinkIndex
    groups[targetSectionIndex].link_list.splice(insertIndex, 0, moved)
    commit(groups)
  }, [cloneGroups, commit])

  const updateSection = (sectionIndex, field, value) => {
    commit(data.map((section, index) => index === sectionIndex ? { ...section, [field]: value } : section))
  }

  const addSection = () => commit([...data, { class_name: '新分类', class_desc: '描述', link_list: [] }])

  const deleteSection = (sectionIndex) => commit(data.filter((_, index) => index !== sectionIndex))

  const addLink = (sectionIndex) => {
    commit(data.map((section, index) => index === sectionIndex
      ? { ...section, link_list: [...section.link_list, { name: '新友链', link: 'https://example.com', avatar: 'https://example.com/avatar.png', descr: '描述' }] }
      : section))
  }

  const updateLink = (sectionIndex, linkIndex, update) => {
    commit(data.map((section, currentSectionIndex) => currentSectionIndex === sectionIndex
      ? { ...section, link_list: section.link_list.map((link, currentLinkIndex) => currentLinkIndex === linkIndex ? update(link) : link) }
      : section))
  }

  const deleteLink = (sectionIndex, linkIndex) => {
    commit(data.map((section, currentSectionIndex) => currentSectionIndex === sectionIndex
      ? { ...section, link_list: section.link_list.filter((_, currentLinkIndex) => currentLinkIndex !== linkIndex) }
      : section))
  }

  const moveLink = (sectionIndex, linkIndex, direction) => {
    const targetIndex = linkIndex + direction
    if (targetIndex < 0 || targetIndex >= data[sectionIndex].link_list.length) return
    const groups = cloneGroups()
    const list = groups[sectionIndex].link_list
    ;[list[linkIndex], list[targetIndex]] = [list[targetIndex], list[linkIndex]]
    commit(groups)
  }

  const hasOptionalFields = (link) => Boolean(
    getScreenshotValue(link) || link.friendslink || link.feeds || normalizeTags(link.tags).length
  )

  if (parseError) {
    return (
      <main className="main-content">
        <div className="sorter-header"><h3>可视化排序</h3></div>
        <div className="sorter-error" role="alert">无法使用可视化编辑器：{parseError}。请切换到文件编辑器修正 YAML。</div>
      </main>
    )
  }

  return (
    <div className="main-content" onPointerMove={handlePointerMove} onPointerUp={handlePointerUp} onPointerCancel={handlePointerUp}>
      <div className="sorter-header">
        <div>
          <h3>可视化排序</h3>
          <p className="sorter-hint">可视化保存会规范化 YAML 格式；需要保留注释或锚点时，请使用文件编辑器。</p>
        </div>
        <div className="sorter-actions">
          <button className="add-section-btn" onClick={addSection}>添加分类</button>
          <button className="save-btn" onClick={onSave} disabled={isLoading}>{isLoading ? '保存中...' : '保存到 GitHub'}</button>
        </div>
      </div>

      <div className="sorter-sections" style={{ opacity: dragState ? 0.7 : 1 }}>
        {data.map((section, sectionIndex) => {
          const isCollapsed = collapsed[sectionIndex]
          return (
            <section key={sectionIndex} className={`sorter-section ${isCollapsed ? 'collapsed' : ''}`}>
              <div className="section-head">
                <button className="collapse-btn" onClick={() => setCollapsed(previous => ({ ...previous, [sectionIndex]: !previous[sectionIndex] }))} aria-label={isCollapsed ? '展开分组' : '折叠分组'}>
                  {isCollapsed ? '▶' : '▼'}
                </button>
                <div className="section-fields">
                  <input className="sorter-input section-name" value={section.class_name || ''} onChange={event => updateSection(sectionIndex, 'class_name', event.target.value)} aria-label="分组名称" placeholder="分类名称" />
                  <input className="sorter-input section-desc" value={section.class_desc || ''} onChange={event => updateSection(sectionIndex, 'class_desc', event.target.value)} aria-label="分组描述" placeholder="分类描述" />
                </div>
                <div className="section-actions">
                  <span className="link-count">{section.link_list.length} 个链接</span>
                  {!isCollapsed && <button className="icon-btn-sm add" onClick={() => addLink(sectionIndex)} aria-label="添加友链">+</button>}
                  <button className="icon-btn-sm danger" onClick={() => deleteSection(sectionIndex)} aria-label="删除分组">×</button>
                </div>
              </div>

              {!isCollapsed && (
                <>
                  {section.link_list.length === 0 ? (
                    <div className="empty-links">该分类暂无友链。</div>
                  ) : (
                    <div className="link-list">
                      {section.link_list.map((link, linkIndex) => {
                        const key = getLinkKey(sectionIndex, linkIndex)
                        const isExpanded = expanded[key] ?? hasOptionalFields(link)
                        return (
                          <div key={linkIndex}>
                            <div className={`insert-zone ${hoverZone === getZoneKey(sectionIndex, linkIndex) ? 'active' : ''}`} data-insert-zone={getZoneKey(sectionIndex, linkIndex)}><div className="insert-line" /></div>
                            <div className="link-sort-item">
                              <button className="drag-handle" onPointerDown={event => handlePointerDown(event, sectionIndex, linkIndex)} aria-label="拖拽排序" title="拖拽排序">⠿</button>
                              <img className="sort-avatar" src={link.avatar || FALLBACK_AVATAR} alt={link.name || '友链头像'} onError={event => { event.currentTarget.src = FALLBACK_AVATAR }} />
                              <div className="sort-fields">
                                <input className="sorter-input field-name" value={link.name || ''} onChange={event => updateLink(sectionIndex, linkIndex, current => ({ ...current, name: event.target.value }))} aria-label="站点名称" placeholder="名称" />
                                <input className="sorter-input field-link" type="url" value={link.link || ''} onChange={event => updateLink(sectionIndex, linkIndex, current => ({ ...current, link: event.target.value }))} aria-label="站点地址" placeholder="链接 URL" />
                                <div className="field-row">
                                  <input className="sorter-input field-avatar" type="url" value={link.avatar || ''} onChange={event => updateLink(sectionIndex, linkIndex, current => ({ ...current, avatar: event.target.value }))} aria-label="头像地址" placeholder="头像 URL" />
                                  <input className="sorter-input field-descr" value={link.descr || ''} onChange={event => updateLink(sectionIndex, linkIndex, current => ({ ...current, descr: event.target.value }))} aria-label="站点描述" placeholder="描述" />
                                </div>
                                <details className="link-extensions" open={isExpanded} onToggle={event => setExpanded(previous => ({ ...previous, [key]: event.currentTarget.open }))}>
                                  <summary>扩展字段</summary>
                                  <div className="extension-grid">
                                    <input className="sorter-input" type="url" value={link.friendslink || ''} onChange={event => updateLink(sectionIndex, linkIndex, current => updateOptionalField(current, 'friendslink', event.target.value))} aria-label="友链页面" placeholder="友链页面 URL" />
                                    <input className="sorter-input" type="url" value={link.feeds || ''} onChange={event => updateLink(sectionIndex, linkIndex, current => updateOptionalField(current, 'feeds', event.target.value))} aria-label="RSS 地址" placeholder="RSS 地址" />
                                    <input className="sorter-input" type="url" value={getScreenshotValue(link)} onChange={event => updateLink(sectionIndex, linkIndex, current => updateScreenshot(current, event.target.value, data))} aria-label="网站截图地址" placeholder="网站截图 URL" />
                                    <input className="sorter-input" value={normalizeTags(link.tags).join(', ')} onChange={event => updateLink(sectionIndex, linkIndex, current => updateTags(current, event.target.value))} aria-label="标签" placeholder="标签，使用逗号分隔" />
                                  </div>
                                </details>
                              </div>
                              <div className="link-row-actions">
                                <button className="icon-btn-sm" onClick={() => moveLink(sectionIndex, linkIndex, -1)} disabled={linkIndex === 0} aria-label="上移友链">↑</button>
                                <button className="icon-btn-sm" onClick={() => moveLink(sectionIndex, linkIndex, 1)} disabled={linkIndex === section.link_list.length - 1} aria-label="下移友链">↓</button>
                                <button className="icon-btn-sm danger" onClick={() => deleteLink(sectionIndex, linkIndex)} aria-label="删除友链">×</button>
                              </div>
                            </div>
                          </div>
                        )
                      })}
                      <div className={`insert-zone ${hoverZone === getZoneKey(sectionIndex, section.link_list.length) ? 'active' : ''}`} data-insert-zone={getZoneKey(sectionIndex, section.link_list.length)}><div className="insert-line" /></div>
                    </div>
                  )}
                </>
              )}
            </section>
          )
        })}
      </div>
    </div>
  )
}
