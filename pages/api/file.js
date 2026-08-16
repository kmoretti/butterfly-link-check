import yaml from 'js-yaml'
import { authMiddleware } from '../../lib/auth'
import { fetchFile, commitFile, AVAILABLE_FILES } from '../../lib/github'

function validateLinkYml(content) {
  try {
    yaml.load(content)
    return null
  } catch (error) {
    return error instanceof Error ? error.message : 'YAML 格式无效'
  }
}

function isGitHubConflict(error) {
  return /sha|conflict|does not match|不是最新/i.test(error instanceof Error ? error.message : '')
}

async function handler(req, res) {
  const { name } = req.query
  if (!name || !AVAILABLE_FILES.find(file => file.name === name)) {
    return res.status(400).json({ error: '无效的文件名' })
  }

  if (req.method === 'GET') {
    try {
      const file = await fetchFile(name)
      if (!file) return res.status(404).json({ error: '文件不存在' })
      return res.json(file)
    } catch (error) {
      return res.status(502).json({ error: error instanceof Error ? error.message : '读取 GitHub 文件失败' })
    }
  }

  if (req.method === 'PUT') {
    const { content, sha } = req.body
    if (typeof content !== 'string') return res.status(400).json({ error: '缺少文件内容' })

    if (name === 'link.yml') {
      const parseError = validateLinkYml(content)
      if (parseError) return res.status(400).json({ error: `YAML 解析失败: ${parseError}` })
    }

    try {
      const result = await commitFile(name, content, sha || undefined)
      return res.json({ success: true, sha: result.content.sha })
    } catch (error) {
      const message = error instanceof Error ? error.message : '保存 GitHub 文件失败'
      return res.status(isGitHubConflict(error) ? 409 : 502).json({ error: message })
    }
  }

  return res.status(405).json({ error: '仅支持 GET/PUT' })
}

export default authMiddleware(handler)
