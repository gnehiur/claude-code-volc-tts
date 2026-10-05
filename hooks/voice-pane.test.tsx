import { test } from 'claude-code/testing'
import type { Register } from 'claude-code'

// 界面冒烟测试：确认各个画面在终端和桌面上都能通过引擎的校验（校验不过时引擎会悄悄改画它自己的）。
// 测试环境里插件读不到预置的 $.state，所以“有音色列表”“朗读中”这两种画面用一个写法相同的内联插件来画。

const PANE_PROPS = { title: '🎙 朗读音色', isFocused: true, bodyColumns: 60, placement: 'dock' } as any

const probe: Register = on => {
  on('ui.render', { component: 'Pane', requestId: 'probe' }, async ($, e) => {
    const { Box, Button, Select, Text } = $.ui.resolve(e)
    return (
      <Box flexDirection="column" gap={1}>
        <Select
          key="voice-pick"
          label="当前音色 "
          options={[{ value: 'a', label: '知性女声 2.0' }, { value: 'b', label: '性感魅惑' }]}
          value="b"
          onSelect={() => {}}
        />
        <Box flexDirection="row" gap={2}>
          <Button key="tts-main" label="⏸" plain onPress={() => {}} />
          <Button key="tts-restart" label="⟲" plain dimColor onPress={() => {}} />
          <Button key="tts-stop" label="⏹" plain dimColor onPress={() => {}} />
          <Button key="tts-speed" label="1.5×" plain dimColor onPress={() => {}} />
          <Button key="tts-voice" label="🎙" plain dimColor onPress={() => {}} />
        </Box>
        <Text bold>添加音色</Text>
      </Box>
    )
  })
}

for (const surface of ['terminal', 'desktop'] as const) {
  test(`音色面板（添加区）在 ${surface} 上能画出来`, async $ => {
    const ui = await $.ui.mount({ plugin: 'tts', surface, component: 'Pane', requestId: 'tts-voice', props: PANE_PROPS })
    for (const key of ['voice-id', 'voice-name', 'voice-add'])
      if (!(await ui.find({ key }))) throw new Error(`没画出 ${key}`)
    await ui.input({ key: 'voice-id', text: 'ICL_x', kind: 'change' })
  })

  test(`下拉框和朗读中的整排按钮在 ${surface} 上能画出来`, { plugins: [{ name: 'probe', register: probe }] }, async $ => {
    const ui = await $.ui.mount({ plugin: 'probe', surface, component: 'Pane', requestId: 'probe', props: PANE_PROPS })
    for (const q of [{ type: 'Select' }, { key: 'tts-main' }, { key: 'tts-voice' }, { text: '添加音色' }])
      if (!(await ui.find(q as any))) throw new Error(`没画出 ${JSON.stringify(q)}`)
    await $.ui.select({ plugin: 'probe', key: 'voice-pick', value: 'a' })
  })
}
