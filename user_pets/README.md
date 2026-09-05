# 用户自备桌宠皮肤

把皮肤文件夹放进这里，桌宠右键菜单「更换皮肤」就能看到：

```text
user_pets/
└── my-pet/
    ├── manifest.json
    ├── idle_0.png      ← 帧文件名随意，manifest 里写对即可
    └── neutral.png
```

`manifest.json` 示例：

```json
{
  "id": "my-pet",
  "name": "我的宠物",
  "width": 160,
  "height": 160,
  "cheer": ["菲比啾比！", "啾比～"],
  "frames": {
    "idle":    ["idle_0.png", "idle_1.png"],
    "distant": ["distant.png"],
    "grumpy":  ["grumpy.png"],
    "neutral": ["neutral.png"],
    "happy":   ["happy.png"],
    "love":    ["love.png"],
    "working": ["working.png"]
  }
}
```

规则：

- 帧文件是相对 `manifest.json` 的 PNG / GIF（Tk 可读格式）。
- 分组含义：`idle` 待机循环；`distant / grumpy / neutral / happy / love`
  对应好感度五档（疏离 / 闹别扭 / 日常 / 心动 / 深爱）；`working` 是调用工具时。
- 缺省分组会回退到 `idle`。
- 换皮后立即生效，无需重启；右键菜单里可随时切回内置占位猫。
- `cheer`：任务完成时头顶冒出的台词，可多句轮换；不配就回退到
  默认「菲比啾比！」。想换成自己的梗，改这里即可。

## 版权提醒

这个目录被 `gitignore` 忽略，只适合**本地个人演示**：

- 菲比 / Furby 是 Hasbro 的形象，直接打包或分发其素材有侵权风险。
- DeepSeek 鲸鱼娘「大肥鱼」等二创形象版权归原作者/项目
  （如 AI_Bento），使用前先确认其素材许可证。
- 简历里展示时建议只用内置占位猫，或换成自己授权/自制的素材。

仓库本身**不会**包含任何第三方角色素材。
