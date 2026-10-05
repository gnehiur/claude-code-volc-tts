// tts 这个 mod 在 $.state 里存的值（会话级）
export type TtsNow = {
  /** 正在念的那段回复的指纹（文本哈希），按钮靠它认出“是不是我这一段” */
  key: string
  status: 'loading' | 'playing' | 'paused'
  /** stream：Python + mpv 流式；buffered：后备的整段合成 */
  mode: 'stream' | 'buffered'
  /** 第几节 / 共几节（流式模式下由 stream.py 报告），例如 2/5 */
  section?: { index: number; total: number }
}

/** 一个保存过的音色，和 ~/.config/volc-tts/voices.json 里的一项相同 */
export type TtsVoice = {
  id: string
  name: string
  /** 验证时试出来的资源：seed-tts-2.0（官方音色）或 seed-icl-2.0（声音复刻） */
  resource: string
}

export type TtsVoiceList = {
  current: string
  voices: TtsVoice[]
}

declare module 'claude-code' {
  interface PluginState {
    tts: {
      now: TtsNow | null
      speed: number
      /** 本会话每轮的最终回复（去掉多余空白后的全文），只有它们下面才画 🔊 */
      finals: string[]
      /** 音色面板：音色列表（读自 voices.json）、两个输入框的草稿、提示语、是否正在验证 */
      voices: TtsVoiceList | null
      draftId: string
      draftName: string
      voiceMsg: string
      voiceBusy: boolean
    }
  }
}
