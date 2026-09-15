# 用户自备桌宠皮肤

把皮肤文件夹放进这里，桌宠右键菜单“更换皮肤”就能看到：

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
  "cheer": ["任务完成喵！", "主人快夸喵～"],
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
  是可选情绪帧；`working` 是调用工具时。
- 缺省分组会回退到 `idle`。
- 换皮后立即生效，无需重启；默认皮肤为 Nyalume。
- `cheer`：任务完成时头顶冒出的台词，可多句轮换；不配就回退到
  默认“任务完成喵！”。想换成自己的台词，改这里即可。

## 版权与发行

这个目录被 `gitignore` 忽略，避免把用户素材误提交到源码仓库：

- 游戏、动画等角色的官方素材或二创图，版权归相应权利人；没有明确授权时只能本地使用；
- 进入官方免费或付费发行包的素材都必须具有再分发权，并登记到根目录
  `ASSET_PROVENANCE.md`；
- 用户自行导入的皮肤不会因此获得官方分发或商业授权。

仓库默认不跟踪 `user_pets/` 下的角色图片；Windows 打包脚本只应使用经过发行审核的官方素材。
