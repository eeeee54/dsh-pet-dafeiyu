# 来源与修改声明

本仓库是 [**MerZlin/dsh-pet-indesktop**](https://github.com/MerZlin/dsh-pet-indesktop)
（MIT License，Copyright (c) 2026 Merzlin，见 [`LICENSE`](LICENSE)）的可发布快照 + 本地修改，
按同一 MIT 许可证分发。原始版权声明保留在 `LICENSE` 与 `THIRD_PARTY_NOTICES.md` 中。

## 相对上游的本地修改

1. **余额专用端点设置行**：`AI 与对话 → 余额与服务状态` 增加「余额接口地址 / 余额 API Key / 测试余额」，
   支持「聊天走别家、余额看 DeepSeek」；Key 存系统钥匙串，不回显、不落明文。
2. **今日已用**：余额差值本地记账（配置目录 `usage.json`），余额气泡第二行显示当天已用金额。
3. **余额「未配置 API Key」根因修复**：钥匙串 Key 解析（`resolve_balance_endpoint`）与
   消费统计的端点解析。

以上每一刀的修改文件说明、性能实测与实机记录见 [`docs/INDEX.md`](docs/INDEX.md) 的「PR 报告存档」。

## 本仓库不含的东西

| 排除项 | 原因 |
|---|---|
| `runtime/` | 绿色部署用的随包 Python 运行时（约 920 MB），不是源码 |
| `data/` | 本地配置、会话与日志（可能含 API Key），绝不入库 |
| `dist/` `build/` `release/` `dist-test/` | 打包产物（数 GB） |
| `assets/characters/*/frameseq/` | 构建期/首跑生成的帧序列派生素材（单角色约 1.4 GB） |

> **发布前要改的两处（否则更新会指回上游）**：
> `pet/updater.py` 的 `REPO = 'MerZlin/dsh-pet-indesktop'` 改成本仓库；
> `update.json` 是更新清单（客户端从仓库根读取），发版时用 `scripts/` 的发布流程按本仓库的
> Release 重新生成，不要直接沿用上游那份。

## 大素材走 Git LFS

角色动画（`assets/characters/**/*.webm`）、OCR 权重（`*.onnx`）、彩蛋图与聊天背景由
[Git LFS](https://git-lfs.com) 管理（见 [`.gitattributes`](.gitattributes)）。
**克隆前请先安装 git-lfs**，否则只会拿到指针文件：

```bash
git lfs install
git clone <repo>
```
