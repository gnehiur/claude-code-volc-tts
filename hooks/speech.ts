// 把 Claude 的 Markdown 回复整理成适合朗读的文字。纯函数，不碰 $，可单独测试：deno test hooks/speech_test.ts
//
// - 代码块略过（念成“这里有一段代码，略过”）
// - 表格逐行念：每行一句，第一格当这一行的名字，其余格念成“表头：内容”
// - 标题、分隔线、粗体行原样留着，stream.py 靠它们切节
// - 剩下的 Markdown 符号（** ` # 等）交给火山服务端过滤

const TABLE_ROW = /^[ \t]*\|.*\|[ \t]*$/
const TABLE_RULE = /^[ \t]*\|?[ \t]*:?-{2,}:?[ \t]*(?:\|[ \t]*:?-{2,}:?[ \t]*)*\|?[ \t]*$/

function cells(row: string): string[] {
  return row
    .trim()
    .replace(/^\|/, '')
    .replace(/\|$/, '')
    .split('|')
    .map(c => c.trim())
}

// 去掉格子里只给眼睛看的符号：粗体星号、反引号、行内链接网址
function clean(cell: string): string {
  return cell
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .trim()
}

function endSentence(s: string): string {
  return /[。！？.!?]$/.test(s) ? s : `${s}。`
}

export function tableToSpeech(lines: string[]): string {
  const hasHeader = lines.length > 1 && TABLE_RULE.test(lines[1])
  const header = hasHeader ? cells(lines[0]).map(clean) : []
  const body = (hasHeader ? lines.slice(2) : lines).filter(l => !TABLE_RULE.test(l))
  const sentences: string[] = []
  for (const row of body) {
    const [first, ...rest] = cells(row).map(clean)
    const parts = first ? [first] : []
    rest.forEach((value, i) => {
      if (!value) return
      const name = header[i + 1]
      parts.push(name ? `${name}：${value}` : value)
    })
    if (parts.length) sentences.push(endSentence(parts.join('，')))
  }
  return sentences.join('\n')
}

export function toSpeech(md: string): string {
  const out: string[] = []
  let table: string[] = []
  const flushTable = () => {
    if (table.length) out.push(tableToSpeech(table))
    table = []
  }
  const noCode = md.replace(/```[\s\S]*?```/g, '（这里有一段代码，略过）')
  for (const line of noCode.split('\n')) {
    if (TABLE_ROW.test(line) || (table.length && TABLE_RULE.test(line))) {
      table.push(line)
      continue
    }
    flushTable()
    out.push(line)
  }
  flushTable()
  return out
    .join('\n')
    .replace(/!\[[^\]]*\]\([^)]*\)/g, '')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/https?:\/\/\S+/g, '链接')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}
