// deno test hooks/speech_test.ts
import { tableToSpeech, toSpeech } from './speech.ts'

function eq(actual: string, expected: string) {
  if (actual !== expected) throw new Error(`\n期望：${JSON.stringify(expected)}\n实际：${JSON.stringify(actual)}`)
}

Deno.test('两列表格：第一格当名字，其余念成 表头：内容', () => {
  eq(tableToSpeech(['| 步骤 | 结果 |', '|---|---|', '| 装 mpv | `0.41.0` |', '| **校验** | 通过 |']),
    '装 mpv，结果：0.41.0。\n校验，结果：通过。')
})

Deno.test('表头有空格子、内容有空格子、带对齐冒号', () => {
  eq(tableToSpeech(['| | 音色 | ID |', '|:--|:--:|--:|', '| → | 性感魅惑 | ICL_x |', '| | 知性女声 | |']),
    '→，音色：性感魅惑，ID：ICL_x。\n音色：知性女声。')
})

Deno.test('没有表头分隔线的表格：直接逗号连起来', () => {
  eq(tableToSpeech(['| a | b |', '| c | d |']), 'a，b。\nc，d。')
})

Deno.test('整段回复：表格换成句子，代码块略过，标题保留', () => {
  const md = '开头。\n\n## 结果\n\n| 测试 | 结果 |\n|---|---|\n| 暂停 | 0.26 秒 |\n\n```py\nx = 1\n```\n\n结尾。'
  eq(toSpeech(md), '开头。\n\n## 结果\n\n暂停，结果：0.26 秒。\n\n（这里有一段代码，略过）\n\n结尾。')
})

Deno.test('表格里的 | 不会误伤普通文字', () => {
  eq(toSpeech('用法：ctl.py pause | resume'), '用法：ctl.py pause | resume')
})
