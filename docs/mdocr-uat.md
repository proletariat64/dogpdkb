# mdocr UAT 报告

Status: 通过 · Date: 2026-08-30 · Commit: `f319c59 feat(mdocr): control supported image formats`

## 测试对象

- 10 份 Yuque 文档（`tmp/mdocr-uat-20260829/input/*.md`，含图 7 份）
- 图片：42 个本地 ref → 41 个唯一 JPEG asset
- 命令：`mdocr ocr -f <file> -o flattened --download [--force]`
- 环境：WSL2，.venv，sing-box 代理 127.0.0.1:13662

## 阶段 1 — prepare（无 OCR）

| 项 | 结果 |
| --- | --- |
| 文件 | 10/10 输出 |
| 图片准备 | 41/41 JPEG |
| 错误 | 0 |
| SVG 跳过警告 | 8（mermaid/puml，按默认白名单，预期） |

## 阶段 2 — ocr

| 文件 | 图 | 成功 | 失败 |
| --- | --- | --- | --- |
| 01-dynamic-waas | 6 | 6 | 0* |
| 02-getstatus | 0 | - | 0 |
| 03-one-portal-tmmf | 14 | 14 | 0 |
| 04-bidv-api | 4 | 4 | 0 |
| 05-whale-cimb | 7 | 7 | 0 |
| 06-zalopay-api | 4 | 4 | 0 |
| 07-aug-report | 0 | - | 0 |
| 08-icbca-hsbc | 6 | 6 | 0 |
| 09-ebc-portal | 1 | 1 | 0 |
| 10-doku-psp-fx | 0 | - | 0 |
| 合计 | 42 | 42 | 0 |

\* 首轮 2 张失败（CDN 超时），重跑补过。

## 失败记录与修复

首轮 2 错误，全在文件 01，全在结果下载阶段：

```text
OCR failed: HTTPSConnectionPool(host='paddleocr-store.bj.bcebos.com', port=443): Read timed out.
OCR failed: HTTPSConnectionPool(host='paddleocr.aistudio-app.com', port=443): Read timed out.
```

### 根因

- PaddleOCR job 服务端完成，结果 JSONL 存 BOS（`bj.bcebos.com`）
- 结果 URL 随机给 `paddleocr-store-N.bj.bcebos.com`
- DNS round-robin 把 store-2..12 + bare 全导到死边缘 `103.235.47.176`（TCP 通、读挂）
- store-1 唯一健康边缘 `182.61.129.11`

### 修复（详见 README.md）

1. `/etc/hosts` 钉 13 条：`182.61.129.11 paddleocr-store{,-1..-12}.bj.bcebos.com`
2. OCR 跑带 `NO_PROXY=...,bj.bcebos.com`（绕过 sing-box 代理再解析）
3. 注意：requests no_proxy 认裸域名，`*.bj.bcebos.com` 不匹配

钉后：剩余全部文件 0 失败；文件 01 重跑 6/6。

## 结论

- mdocr 格式控制实现（默认白名单 png/webp/gif/jpg/jpeg/tif/tiff + `--format` + JPEG 原字节 + 下载前过滤）正常
- 42/42 图片 OCR 成功，0 残留错误
- 唯一外部阻塞 = 百度 BOS CDN 边缘故障，客户端已钉 IP 绕过，非 mdocr bug

## 附

- 输出：`tmp/mdocr-uat-20260829/flattened/`（10 md + assets/41 jpg，6.1M）
- 输入：`tmp/mdocr-uat-20260829/input/`（7.8M）
- 后续若百度轮换边缘 IP，验证 `182.61.129.11` 再跑；新 store-N 后缀出现则补 hosts 行
