# 现有中文补丁

用户指定角色姓名保留日文。补全不替换角色姓名标签、头像上的姓名或角色介绍中的姓名；已有姓名译图已从生成批次撤回。其他日文继续处理，已有中文和其他语言保持原样。

当前本地候选批次合计 103 张资源：6 张确认窗口、65 张文字标签、9 张区域标签、4 张相册按钮、3 张相册提示、1 张标题画面、1 张操作说明和 14 张局部覆盖贴图。已验证合并后原补丁 204 个条目保持字节不变。该数字只表示资源生成与合并，不表示全游戏语言覆盖；部分说明图片仍含日文，场景与影片也尚未完成审查，运行验收未完成。

本地文字重绘已获得用户授权，使用 Pillow 与中文字体，不依赖图像生成服务。`tools/render_localization_dialogs.py` 可重绘六张确认窗口，并校验原尺寸、原 alpha 和文字矩形外的像素不变。生成结果及合并补丁位于忽略的 `out/` 目录，尚未完成游戏运行验收，不能作为完整汉化发布。

预览原始 TGA 时，若图片查看工具报告不支持格式，先用 Pillow 转存为 PNG，再查看 PNG。原始 TGA 保持不变；这是预览格式兼容问题，不能记录为图像生成或安全系统拒绝。只有实际工具调用返回拒绝时才可报告拒绝。

v0.1.1 原样套用游戏资源中已有的汉化补丁。`Data/patch.pp` 已包含 27 个 GBK 编码的剧本补丁、字体和图片补丁；基础剧本则为 CP932 日文。原汉化组署名保留。

后续补全工作只处理剩余日文，保留已有中文与其他语言。待处理内容和译文记录在 `ios/localization/ja-zh-CN.json`，各条目状态明确区分译文、贴图制作和运行验证，不能将清单存在视为游戏已经完成汉化。

`tools/biko3_assets.py` 提供了只读解析工具：

```powershell
python tools/biko3_assets.py <Data> out/localization-inventory.json --inventory
python tools/biko3_assets.py <Data> out/scenarios --export-scenarios
python tools/biko3_assets.py <Data> out/patch-audit.json --audit-patch
python tools/biko3_assets.py path/to/texture.tga out/texture-preview.png --preview-texture
```

这些命令只读检查索引和补丁后的剧本，不修改原始资源。原程序查找 `game/patch.pp`，而分卷展开到 `game/Data/patch.pp`。文件适配层仅在根目录补丁不存在时，将只读访问回退到 `Data/patch.pp`；根目录补丁优先，写入不重定向。原有资源清单及补丁字节均不改变。

v0.1.1 已在 macOS 确认补丁加载和中文菜单显示。新增补全尚未发布，也未通过运行验证。只读导出最终界面资源可使用 `--effective-ui`；该命令将原补丁应用到 575 个界面资源中的对应条目，避免把已经被补丁替换的原日文误计为遗漏。

更新实现支持将完整合并补丁放在 `ios/localization/patch.pp`，并用 `bundle.json` 记录大小、SHA-256、原始补丁哈希和人工审查状态。staging 校验后将其复制到应用包的 `dm/localization`；启动时使用应用包里的补丁，Documents 内原始 Data 保持不变。更新 IPA 即可更新这一补丁，不依赖已经删除的六个资源分卷。

当前仅完成加载与打包代码及部分测试，尚未制作完整补全资源，未在 iPhone 验证升级加载。没有 `patch.pp` 和配套清单时仍保持 v0.1.1 行为；不能将代码已接入描述为汉化已经完成。最终发布前会明确覆盖安装结果和是否需要新数据。
