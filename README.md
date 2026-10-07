# claude-code-volc-tts

一个 [Claude Code](https://claude.com/claude-code) mod：在每轮回复的**最终回复**下面加一个 🔊 按钮，用**火山引擎豆包语音**流式朗读。音色可以在面板里自己添加、切换（默认「知性女声 2.0」）。桌面 App 的 Code 标签页和终端版都能用。

```
没在念：   🔊
正在念：   ⏸   ⏮   ⏭   ⟲   ⏹   1×   🎙
已暂停：   ▶️   ⏮   ⏭   ⟲   ⏹   1×   🎙
```

## 功能

- **流式朗读**：边合成边播放，首声约 0.8 秒，和回复长短无关
- **三态主按钮**：🔊 开始 → ⏸ 暂停 → ▶️ 从停下的地方继续
- **⏮ ⏭ 按节跳转**：节的边界是 `##` 标题、`---` 分隔线和单独一行的粗体（例如结尾的 **这次攒下了什么**）；没有标题的回复按段落切。⏮ 在本节念了 3 秒以上时回到本节开头，否则回到上一节；在最后一节按 ⏭ 结束朗读。状态栏显示进度，例如 `🔊 火山朗读中 2/5`
- **⟲ 从头念 / ⏹ 停止**：只在念或暂停时出现
- **代码块略过，表格逐行念**：代码块念成"这里有一段代码，略过"；表格每行念成一句，第一格当这一行的名字，其余格念成"表头：内容"，例如 `| 装 mpv | 0.41.0 |`（表头"结果"）念成"装 mpv，结果：0.41.0。"
- **倍速**：1× → 1.25× → 1.5× → 2× 循环，音调不变，记住上次的选择
- **只在最终回复显示**：Claude 干活过程中的说明文字不加按钮
- **全局只有一个声音**：在任何会话里开始新的朗读，旧的自动结束
- **切走会话自动暂停**（仅桌面 App）：切回来点 ▶️ 继续
- **本地缓存**：按节缓存，同一节再念不调用火山、不花钱，0.2 秒出声；保留 7 天、上限 200MB
- **音色面板**：点 🎙 或输入 `/voice` 打开；下拉切换、试听、删除，填音色 ID 验证通过才保存
- **后备模式**：mpv 或 Python 不可用时，退回整段合成后播放（只能停止，不能暂停）

## 依赖

- macOS（用到了 `/usr/bin/python3`、`afplay` 和 Claude 桌面 App 的日志路径）
- Claude Code **2.1.259 及以上**（需要 mod / function hooks 功能）
- [mpv](https://mpv.io/)：`brew install mpv`
- 火山引擎账号，开通「豆包语音合成大模型 2.0」，在控制台 **API Key 管理** 里创建一个 API Key

## 安装

1. 克隆到任意位置，例如：

   ```bash
   git clone https://github.com/gnehiur/claude-code-volc-tts.git ~/Projects/claude-mods/volc-tts
   ```

2. 放好 API Key（只有你自己可读）：

   ```bash
   mkdir -p ~/.config/volc-tts && chmod 700 ~/.config/volc-tts
   ```

   ```bash
   (umask 077; printf '%s' '你的 API Key' > ~/.config/volc-tts/api_key)
   ```

3. 在 `~/.claude/settings.json` 里让 Claude Code 加载它（多个 mod 用 `:` 分隔）：

   ```json
   {
     "env": {
       "CLAUDE_CODE_PLUGIN_DIRS": "/Users/<你>/Projects/claude-mods/volc-tts",
       "CLAUDE_CODE_PLUGIN_DIR_WATCH": "1"
     }
   }
   ```

   `CLAUDE_CODE_PLUGIN_DIR_WATCH` 可选：开了以后改代码会自动重载。桌面 App 的会话默认不监视这个目录。

4. 新开一个会话，等 Claude 回复完，最终回复下面就会出现 🔊。

## 换音色

点朗读时那排按钮最后的 🎙，或者在输入框输入 `/voice`，打开音色面板：

```
┌ 🎙 朗读音色 ─────────────────────────────┐
│ 当前音色  [ 知性女声 2.0            ▾ ]  │  选中即切换，下一次朗读生效
│           🔊 试听   🗑 删除               │
│ 添加音色                                  │
│ 音色 ID   [ ICL_uranus_zh_female_…     ]  │
│ 名字      [ 性感魅惑 ]         （可选）   │
│           [ 验证并保存 ]                  │
└───────────────────────────────────────────┘
```

- 音色 ID 在火山控制台的 **音色库** 里找，例如 `zh_female_zhixingnv_uranus_bigtts`、`ICL_uranus_zh_female_xingganmeihuo_tob`。
- **验证**就是真的合成一句「你好，我是 xxx。」（约 10 个字，会计费）：先用 `seed-tts-2.0`，火山回复"音色和资源对不上"（错误码 55000000）时再试 `seed-icl-2.0`（声音复刻音色）；都不行就判定不存在或你的账号没开通，不会保存。
- 验证通过的音色存进 `~/.config/volc-tts/voices.json`，所有会话共用；验证时合成的那句直接写进缓存，所以紧接着的试听不再计费。
- 正在念的时候换音色，当前这段不变，下一次朗读用新音色；想马上听，点 ⟲。

命令行也能管：`python3 bin/voices.py list | use <ID> | remove <ID> | add <ID> [名字]`。

## 工作原理

```
🔊 按钮（hooks/register.tsx，运行在 Claude Code 里）
  │ $.process.spawn，把整理好的文字从 stdin 交给
  ▼
bin/stream.py ──HTTP Chunked──▶ 火山 /api/v3/tts/unidirectional
  │ 收到一块 PCM 就写一块
  ▼
mpv（开着 IPC 遥控口 ~/.config/volc-tts/mpv.sock）
  ▲
  │ pause / resume / stop / speed
bin/ctl.py ◀── ⏸ ▶️ ⏹ 倍速 按钮
```

- mod 不能直接收发流式数据（`$.http.fetch` 要等全部内容收完才返回），所以"边收边播"交给一个本地 Python 脚本和 mpv。
- `stream.py` 订阅 mpv 的暂停、倍速和播放位置，在 stdout 报告 `STATE playing|paused`、`SPEED x`、`SECTION 2/5`，mod 据此重画按钮和状态栏。
- **按节合成，语气连贯**：每节单独请求、单独缓存；同一次朗读的所有请求带同一个 `section_id`，由一个下载线程按顺序一次发一个，服务端据此记住前文，下一节接着前面的语气念（仅 2.0 音色和声音复刻 2.0 音色支持）。正在念第 k 节时预取到第 k+2 节（火山流式接口大约按实时速度返回，不预取的话跳过去要等）。跳节时停掉当前 mpv，从目标节的音频重新开一个，因为 mpv 读的是管道，不能往前跳。
- 只给最终回复加按钮：用 `turn.complete` 事件里的 `answer` 认出每轮的最终文字；会话启动时用 `$.session.messages()` 补上历史回复。
- 朗读前会去掉代码块、图片和链接网址；剩下的 Markdown 符号交给火山服务端过滤（`disable_markdown_filter`）。
- `bin/ctl.py` 在终端里也能用：`python3 bin/ctl.py pause`、`python3 bin/ctl.py next`、`python3 bin/ctl.py speed 1.25`。

## 排查问题

每次朗读和添加音色的过程都记在 `~/.cache/volc-tts/stream.log`（音色、请求了几段、首声时间、错误原因），超过 1MB 自动轮换成 `stream.log.1`：

```bash
tail -20 ~/.cache/volc-tts/stream.log
```

## 已知局限

- **仅 macOS。**
- **"切走会话自动暂停"依赖 Claude 桌面 App 未公开的日志格式**（`~/Library/Logs/Claude/main.log` 里的 `LocalSessions.setFocusedSession:`）。App 更新后可能失效，表现为切走后继续念，不会报错。
- **mod 接口仍处于早期阶段**，Anthropic 说明它可能在版本之间变化，本插件随时可能需要跟着调整。
- 火山按合成的字数计费，Markdown 符号也算字数；用过的内容走本地缓存，不会重复计费。

## 文件

| 文件 | 作用 |
|---|---|
| `.claude-plugin/plugin.json` | 插件清单 |
| `hooks/hooks.json` | 声明 hooks 模块 |
| `hooks/register.tsx` | 按钮、状态、事件钩子 |
| `types/index.d.ts` | `$.state` 里存的值的类型 |
| `bin/stream.py` | 流式合成 + mpv 播放 + 切走暂停 + 缓存 |
| `bin/volc.py` | 火山接口、缓存路径、音色列表（两个脚本共用） |
| `bin/voices.py` | 音色列表的查看、切换、删除、验证并添加 |
| `hooks/speech.ts` | 把回复整理成朗读文字（代码块略过、表格转句子） |
| `hooks/speech_test.ts` | 上面那个的单元测试：`deno test hooks/speech_test.ts` |
| `hooks/voice-pane.test.tsx` | 界面冒烟测试：`claude plugin test .` |
| `bin/ctl.py` | 遥控正在进行的朗读 |
| `tsconfig.json` | 编辑器类型检查用；它引用的 `.claude-plugin/types/` 由引擎生成、不在仓库里，在 Claude Code 里运行 `/plugin-types` 即可生成 |

## 许可

[MIT](LICENSE)
