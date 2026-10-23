# Switch 上游与 iOS 资源核对

核对日期：2026-10-23（Asia/Shanghai）。上游为 [ruje0504-2/biko3-switch-runtime](https://github.com/ruje0504-2/biko3-switch-runtime)，已将其加入 README 致谢与固定参考仓库清单。

本次核对的源码提交为 `63fddc3830b991e725657689934c3b6e3b5786bb`，提交时间为 2026-10-19 02:14:09 +08:00。源码参考版本与可运行 NRO 版本分别记录，更新参考快照不会自动替换 iOS 内嵌程序。

## 上游新增内容

比较 Switch 1.0.79 对应源码提交 `8a4bddb` 与上述提交，共有四次新增提交：

| 日期（北京时间） | 提交 | 内容 | 对当前 iOS 移植的影响 |
| --- | --- | --- | --- |
| 2026-10-18 | `ac9edb3` | SDL3 + Vulkan Linux 桌面后端，全屏、键鼠、触屏、最多四个手柄及音频支持 | 是另一套平台后端，当前 iOS 使用 nxnative 与 MoltenVK，不能通过替换 Data 获得这些支持 |
| 2026-10-18 | `8e03723` | Steam 快捷方式与启动器说明 | Linux 使用说明 |
| 2026-10-18 | `17fb8bf` | 恢复每次呈现时使用原游戏时钟步长，修复桌面慢动作 | 撤销新增桌面分支的限步逻辑；未提供新的 Switch NRO，不能据此声称已更新 iOS 程序 |
| 2026-10-19 | `63fddc3` | 一键 Steam 安装脚本、库快捷方式与封面及 Linux 文档 | Linux / Steam 安装支持 |

依据上游 README、上述源码差异与 GitHub Releases 查询，未发现新增剧情、角色、关卡资源，也没有可下载的 Release 附件。上游提及的 `交付/`、`local/` 与游戏素材不在 Git 源码中；获取源码不会下载完整游戏或新补丁。

上游 README 记录的五人选择、结局、鉴赏、相册、存读档、雨雪与中文流程等功能在 1.0.79 之前或该版本中已接入，不能当作本轮新增内容，也不能代替 iPhone 真机验证。

## 当前 iOS 资源核对结果

| 项目 | 本地结果 | 上游依据 |
| --- | --- | --- |
| `switch/biko3/biko3-runtime.nro` | 16,408,236 字节；SHA-256 `31320a8ce44f8e1826fc90ed5b26cb714c551413b286f8c69adb03f9e8b07178` | 上游 README 公布的正式 NRO 1.0.79 大小与哈希一致 |
| `switch/biko3/game/Data/patch.pp` | 37,165,253 字节；SHA-256 `5aa69af72f240d6cf2fa1d12293564256bef120b1c1a70cb210e597927df9f42` | 上游 `reports/patch-loading-verification.json` 的补丁哈希一致 |
| 原版 Data | 除补丁外共 168 个文件、2,945,456,435 字节 | 数量与总字节数匹配上游 `reports/nsp-1.0.78-verification.json`；该报告未提供全部文件哈希，因此不宣称已逐文件与上游素材比较 |
| iOS 资源包 | 169 个文件、2,982,621,688 字节、6 个分卷 | 分卷与本地 Data 逐文件 SHA-256、大小、分段布局全部校验通过 |
| 内置清单 | 与 `out/resource-packs/Biko3Data.manifest.json` 字节一致 | 保留现有资源包身份，继续兼容已有下载 |

本地源文件清单身份 SHA-256：`8076d499c762b0c33c2495b49c5a5c66ffd485c289247ca7c3b69a9e8a9203b6`。

本次实际执行：

```powershell
python tools/pack_resources.py verify --parts out/resource-packs --data switch/biko3/game/Data
```

结果为 `verified: true`、`source_verified: true`。分卷与源文件都已验证；这不是 iPhone 运行测试。

## 更新结论

本次更新上游参考版本、来源致谢及资源核对记录；没有发现需要替换的新游戏资源，保留现有 NRO、六个分卷与内置清单。已有玩家无需重新下载或复制约 3 GB 数据，也不需要因本次核对更新 IPA。

`ios/localization/patch.pp` 是本项目此前生成、尚未完成运行验收的菜单汉化候选，不是上游新资源。本轮未将该候选合入 Data 或分卷，也未发布新的 IPA / Release。

以后取得新的 NRO 或补丁后，应分别核对兼容性：替换 NRO 会影响固定哈希、地址钩子与 iOS 构建；替换 Data 文件会改变分卷哈希，需要同步内置清单与对应 IPA。不能直接覆盖已发布分卷并继续沿用旧清单。
