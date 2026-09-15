# Nyalume 桌宠表情帧流水线

把“同一个原创人设生成多个表情帧”做成可复现的本地 ComfyUI 管线。
生成的工作流与提示词都在本目录（tracked）；生成的图片素材只写进
gitignore 的 `user_pets/`，不进源码仓库。

> 商业发行注意：2026-09-15 之前的素材使用过许可记录不完整的 StarSea pastel v5、
> z3 画风 LoRA 和鬼针草 LoRA，不能直接进入付费发行包。当前默认工作流已改用
> 许可明确允许商业使用的 Animagine XL 4.0 Opt，并移除全部 LoRA；旧图片仍须重新生成。
> 完整结论见根目录 `ASSET_PROVENANCE.md`。

## 文件

- `prompts/q_BA_prompt.txt`：商业重生成提示词（站姿立绘、银白侧马尾、
  橘子发饰、粉白哥特裙、纯白丝袜、左腿腿环、深蓝平涂底，所有权重 ≤1.5）
- `workflows/q_BA_workflow_api.json`：商业素材候选 API 工作流
  （Animagine XL 4.0 Opt → cfg5/Euler a/28 steps → 脸/手 FaceDetailer → SaveImage，
  不加载第三方 LoRA）
- `make_moods.py`：按表情词生成 6 份表情工作流
  （distant/grumpy/neutral/happy/love/working）
- `comfy_submit.py`：把 API 工作流 POST 到 127.0.0.1:8188 队列尾
- `comfy_wait.py`：轮询 /history 直到出图，打印输出文件名
- `pet_skin_cut.py`：把纯色底 PNG 抠成透明皮肤帧（边框连通抠除，
  支持 --adaptive 处理渐变背景，--out 指定输出目录）

## 前置条件

1. 本机 ComfyUI（aki 整合包）正在运行，API 端口 8188；
2. 模型就位：
   - checkpoint：`animagine-xl-4.0-opt.safetensors`
   - 上游与许可证：<https://huggingface.co/cagliostrolab/animagine-xl-4.0>
     （CreativeML Open RAIL++-M，模型卡明确允许商业使用）
   - 不使用第三方 LoRA
   - ultralytics：`bbox/face_yolov8m.pt`、`bbox/hand_yolov8s.pt`
3. Python 依赖（项目 .venv）：
   `pip install pillow numpy scipy`

## 用法

```bash
# 1) 生成 6 个表情工作流（可加 happy love 只生成指定表情）
python tools/nyalume_pipeline/make_moods.py

# 2) 逐个排队出图
python tools/nyalume_pipeline/comfy_submit.py user_pets/nyalume/art/moods/happy_workflow_api.json
python tools/nyalume_pipeline/comfy_submit.py user_pets/nyalume/art/moods/love_workflow_api.json

# 3) 等出图
python tools/nyalume_pipeline/comfy_wait.py <prompt_id...>

# 4) 把 ComfyUI output 里的 nyalume_mood_<key>_*.png 拷到
#    user_pets/nyalume/moods_raw/<key>.png，然后抠成 300x300 透明帧：
python tools/nyalume_pipeline/pet_skin_cut.py \
  user_pets/nyalume/moods_raw/happy.png --prefix mood_happy_ --out user_pets/nyalume

# 5) 把 mood_<key>_1.png 改名成 user_pets/nyalume/mood_<key>.png，
#    并在 user_pets/nyalume/manifest.json 的 frames 里填上对应文件
```

## 提示词维护约定

- 所有 `(tag:weight)` 权重不得超过 1.5；
- 背景必须是和“银白头发+粉白衣物”色差大的深蓝平涂，否则抠图会吃发丝；
- 表情词只在 `looking at viewer, ` 之后插入，姿势/服装/头发标签不动，
  保证 6 帧共用同一构图种子（默认 2026097002）。

## 版权与溯源

角色概念和提示词由本项目维护，但模型许可不会自动保证输出不侵犯第三方权利。
商业版必须从文字设定重新生成，不使用旧图片作参考，并保存模型文件 SHA256、
许可证副本、完整工作流、seed 和人工修改记录。生成的 PNG 放在 `user_pets/`
（gitignore）；是否进入发行包由根目录 `ASSET_PROVENANCE.md` 的状态决定。
