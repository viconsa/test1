# wechat-export — 微信聊天记录导出工具

把**你自己的**微信聊天记录导出为 HTML（仿微信气泡界面）、TXT、CSV（Excel 可直接打开）、JSON。

| 来源 | 需要什么 | 难度 |
|---|---|---|
| **iPhone**（推荐） | 电脑上的 iTunes / Finder **未加密**本地备份 | ★ 最简单，无需密钥 |
| **Windows 微信 3.x** | `WeChat Files\<wxid>\Msg` 目录 + 32 字节数据库密钥 | ★★ |
| **Android** | root 后取出的 `EnMicroMsg.db` + uin | ★★★ |

> 只支持纯 Python + `cryptography`，可在 Windows / macOS / Linux 上运行，不会修改任何原始数据（所有数据库均以只读方式打开）。

## 安装

```bash
cd tools/wechat-export
pip install -r requirements.txt     # Python 3.9+
```

## 用法

### 1. iPhone（推荐）

1. 用数据线连接电脑，在 iTunes（Windows）或 Finder（macOS）中选择“将 iPhone 上的所有数据备份到这台电脑”，**不要勾选“加密本地备份”**，点“立即备份”。
2. 找到备份目录（里面有 `Manifest.db`）：
   - macOS：`~/Library/Application Support/MobileSync/Backup/<一串ID>/`
   - Windows：`%APPDATA%\Apple Computer\MobileSync\Backup\<一串ID>\` 或 `%USERPROFILE%\Apple\MobileSync\Backup\<一串ID>\`
3. 导出：

```bash
# 先看看有哪些会话
python -m wechat_export ios "/path/to/Backup/<ID>" --list

# 全部导出为 HTML + TXT（默认）
python -m wechat_export ios "/path/to/Backup/<ID>" -o 导出结果

# 只导出某个人/群，2024 年的，导出为 CSV 和 JSON
python -m wechat_export ios "/path/to/Backup/<ID>" -c 张三 -c 家庭群 --since 2024-01-01 --until 2025-01-01 -f csv,json
```

如果一台手机登录过多个微信号，工具会列出账号哈希，用 `--account <哈希>` 选择。

### 2. Windows PC 版微信（3.x）

PC 版数据库是 SQLCipher 加密的，需要你提供**本人账号**的 32 字节密钥（64 位十六进制字符串）。
本工具**不包含**从微信进程内存中提取密钥的功能，请自行获取（例如使用 GitHub 上的开源工具，在自己的电脑上、自己登录的微信上获取）。

```bash
# 验证密钥是否正确
python -m wechat_export decrypt pc "C:\Users\你\Documents\WeChat Files\wxid_xxx\Msg\MicroMsg.db" -k <64位十六进制密钥> --verify-only

# 解密并导出（解密后的数据库会保存在 <输出目录>\_decrypted，可以用 DB Browser for SQLite 查看）
python -m wechat_export pc "C:\Users\你\Documents\WeChat Files\wxid_xxx\Msg" -k <密钥> -o 导出结果

# 之后再导出就不用再解密了
python -m wechat_export pc --decrypted 导出结果\_decrypted -f csv
```

> 导出前建议先**退出微信**，或把 `Msg` 目录复制一份再处理，避免文件被占用或数据库未写完。
> 若聊天记录只在手机上，可以先在 PC 微信“设置 → 聊天 → 聊天记录迁移与备份”把手机记录迁移到电脑。
> **微信 4.x** 的数据库格式完全不同，目前不支持。

### 3. Android

需要已 root 的手机（或通过其它方式拿到 `/data/data/com.tencent.mm/` 下的文件）：

- 数据库：`/data/data/com.tencent.mm/MicroMsg/<32位哈希>/EnMicroMsg.db`
- uin：`/data/data/com.tencent.mm/shared_prefs/auth_info_key_prefs.xml` 中 `_auth_uin` 的值（可能为负数）

```bash
python -m wechat_export android EnMicroMsg.db --uin <uin> -o 导出结果
# 老手机若微信用的是真实 IMEI：
python -m wechat_export android EnMicroMsg.db --uin <uin> --imei <IMEI> -o 导出结果
# 已知 7 位密码：
python -m wechat_export android EnMicroMsg.db --password abc1234 -o 导出结果
```

## 通用选项

| 选项 | 说明 |
|---|---|
| `-o, --out` | 输出目录，默认 `wechat_export_output` |
| `-f, --format` | `html,txt,csv,json` 任意组合，默认 `html,txt` |
| `-c, --chat` | 按备注/昵称/微信号/wxid 关键字筛选会话，可重复 |
| `--since` / `--until` | 时间范围，如 `2024-01-01`（`--until` 不含当天） |
| `--me` | 导出时“我”的显示名，默认“我” |
| `--list` | 只列出会话和消息数，不导出 |

输出结构：

```
导出结果/
  html/index.html      ← 用浏览器打开，点进各个会话
  html/<会话名>.html
  txt/<会话名>.txt
  csv/<会话名>.csv
  json/<会话名>.json   ← 含原始内容 raw 字段，方便二次处理
```

## 支持的消息类型

文字、系统提示/撤回、链接、文件、引用回复、转账、位置、名片均会解析成可读文字；
图片、语音、视频、表情显示为 `[图片]` 等占位符（**不导出媒体文件本身**）。

## 测试

```bash
pip install sqlcipher3-binary   # 仅测试需要，用来生成真实的 SQLCipher 加密库
python -m unittest discover -s tests -v
```

测试会为三个平台分别构造模拟数据库并跑完整的命令行流程。

## 说明与免责

- 仅用于导出、备份**你本人**的聊天记录。未经他人同意获取他人的聊天记录可能违法。
- 微信数据库格式随版本变化，本工具基于公开资料实现，未覆盖所有版本，遇到问题欢迎反馈。
- 本项目与腾讯公司无关。
