# Nyalume 素材来源与商业发行状态

更新日期：2026 年 9 月 15 日

本文件用于回答两个问题：素材是怎样生成的，以及它是否可以进入付费发行包。模型作者允许商用，不等于输出必然不侵犯第三方权利；提示词、参考图、角色设计和人工后期也必须有合法来源。

## 当前素材结论

| 素材 | 已知生成链 | 商业发行状态 |
| --- | --- | --- |
| `user_pets/nyalume/` 桌宠帧 | StarSea pastel v5 + z3 画风 LoRA + 鬼针草 Illustrious/NoobAI LoRA，部分早期帧仅记录 StarSea pastel v5 | **未批准**：模型来源或商业条款未完整保存，不能进入付费发行包 |
| `nyalume/frontends/web/static/daily_nyalume/` 每日卡片 | 部分设计记录了参考图和分层过程，但未完整记录生成服务、模型版本和适用条款 | **待补证**：在来源补全或重新生成前，不作为可商用素材 |
| 程序绘制的无素材占位猫 | `nyalume/frontends/pet/renderer.py` 中的程序图形 | **允许**：项目自有代码生成，不依赖外部角色素材 |
| 用户自行导入的皮肤和壁纸 | 用户提供 | **由用户负责**：不得随官方商业包再次分发，除非取得书面授权 |

## 如何从工作流识别底模和 LoRA

ComfyUI API 工作流是 JSON。查看以下节点即可：

- `CheckpointLoaderSimple.inputs.ckpt_name`：底模或 checkpoint；
- `LoraLoader.inputs.lora_name`：LoRA 文件；
- `strength_model` / `strength_clip`：LoRA 权重。

旧工作流 `tools/nyalume_pipeline/workflows/q_BA_workflow_api.json` 曾明确记录：

- checkpoint：`绘星海_清新风格 StarSea_pastel _v5.safetensors`；
- LoRA：`z3画风_illv0.1.safetensors`，model/clip 权重 0.4；
- LoRA：`鬼针草-Illustriou_NoobAI_1.0.safetensors`，model/clip 权重 0.8。

历史生成文件仍可能在被 Git 忽略的 `user_pets/nyalume/art/` 中；它们仅作过程留档，不能证明商用授权。

## 新的商业素材工作流

从 2026 年 9 月 15 日起，仓库默认工作流改用 **Animagine XL 4.0 Opt**，不再加载第三方 LoRA：

- 上游：<https://huggingface.co/cagliostrolab/animagine-xl-4.0>；
- 文件：`animagine-xl-4.0-opt.safetensors`；
- 许可证：CreativeML Open RAIL++-M；模型卡明确允许商业使用、修改和分发，并要求保留许可证与声明。

此替换只让新的生成链具备清晰许可，不会自动“洗白”旧图片。付费版应从文字角色设定重新生成全部桌宠帧和卡片，不使用旧图片做图生图参考，并保存以下记录：

1. 模型页面、许可证副本、下载日期和模型文件 SHA256；
2. 完整提示词、负面提示词、seed、工作流 JSON；
3. 所有参考图的作者、授权范围和授权凭证；
4. 人工修改记录与最终文件 SHA256；
5. 与已知游戏、动画或他人角色的相似性复核。

更保守的方案是委托画师从文字设定重新设计，并取得包含商业发行、修改、宣传和衍生素材权利的书面授权。
