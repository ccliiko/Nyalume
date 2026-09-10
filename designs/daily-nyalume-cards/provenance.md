# 今日 Nyalume 卡片素材说明

十张新增卡面以 `designs/daily-nyalume-card-89-99/assets/subject.png` 作为角色身份参考生成，输出为 1024×1536 PNG，并放入 `nyalume/frontends/web/static/daily_nyalume/`。旧的 `lucky.png` 保留用户确认的 89–99 分设计。

共同身份锚点：银白淡紫高马尾、粉紫眼、粉色花结、银色小冠、白粉服装与大头小身体比例。卡面不烘焙标题或分数，精确文字由界面渲染；上下各留约 15% 的安静区域。

光泽由低到高依次为：`matte`、`satin`、`pearl`、`mica`、`opal`、`prism`、`aurora`、`stardust`、`linear`、`holo`、`crystal`。所有新增卡面只使用银白、珍珠与彩色冷光；不使用金箔、金框或“金卡”标识，金卡视觉留给未来付费掉落。

界面在底图上叠加随指针移动的光泽层，并遵循 `prefers-reduced-motion`。底图负责场景与人格，CSS 光泽负责档位差异，以免把动态反射烘死在图片中。
