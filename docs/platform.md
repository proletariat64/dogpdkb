# dogpdkb 交付与预检

## 交付边界

Python distribution `dogpdkb` 提供编排器、独立 mdocr/mdcompare 命令与禁用的
配置示例。Graphify、Skylark 的运行环境独立；Graphify、Yuque 禁止修改源码，Skylark 使用已恢复的 CLI；不把工具运行时放入编排器。配置、凭证、真实来源表、语料、图谱、缓存与
run 都不在 wheel 内。旧 workflow 脚本、旧状态和旧参数不兼容。

日常入口为 discover、apply、update-graph、sync；status 只读；publish-web 与
update/uninstall 独立。sync 不发布 Web，不自动清理历史目录。
支持 Python 3.10+ POSIX；开发/CI 使用 Python 3.12，Windows 使用 WSL。

## 安装配置

复用合适环境；需要新建时选择用户级独立环境。源码开发使用 make bootstrap。
[Linux managed 安装与生命周期](installing.md) 保留原行为，本次不发布新版本。

```bash
make check
make build
make smoke

dogpdkb init-config -o ./instance
# 编辑 instance/books.json 的路径与来源并启用
# 在 instance/dogpdkb.toml 指定所需公开工具入口
dogpdkb doctor --config ./instance/dogpdkb.toml --book funds
dogpdkb init-state --config ./instance/dogpdkb.toml --book funds
```

init-config 拒绝覆盖现有文件或 symlink。显式 --config 优先，其次
DOGPDKB_CONFIG；源码默认 default.toml，安装包默认调用目录 dogpdkb.toml。
TOML 路径相对配置，来源 corpus 相对 books.json，凭证保持工具环境。

需要具备的公开工具合同：

- [mdcompare catalog/knowledge/compare 与 mdocr plan/run](orchestrator-tool-contracts.md)。
- [Graphify 原版 extract](orchestrator-graphify-contract.md)，使用目录级原生增量及隔离输出。
- [Skylark listing/export](orchestrator-skylark-contract.md)，使用已恢复的独立 CLI。

工具入口可为 executable 或 argv 数组，不能是 shell 命令文本。Graphify 后端、
mdocr 认证与工具内部参数由工具配置负责。Graphify 使用原版 CLI，已有图必须保留原生 manifest；Skylark 需要独立 CLI 入口。
本地验证不代表工具已全局安装。

## Doctor 与验证边界

`dogpdkb doctor --package` 提供离线包检查；正常 doctor 读取配置和选中来源，
调用配置的工具 --help，JSON 模式输出检查数组。它不导入工具包、不请求认证，
不证明远端可用或完整业务合同。配置/命令不可用时返回1。真实连接、付费处理和
生产 UAT 必须另外记录。

make smoke 检查 wheel/sdist 内容，在临时环境安装 wheel，清除源码路径与生产
配置覆盖，证明加载安装包。然后通过公开 CLI 验证 init-config、status、计划
检查点、显式恢复、正式语料与图谱结果；mdcompare 和无图片 mdocr 实跑，
Skylark/Graphify 是明确的外部进程替身。没有假装安装 Graphify 内部 Python 包。
安装/构建使用本机缓存，缺依赖时报告失败，不自动调用生产或付费服务。

make check 与 make smoke 是本地证据。GitHub CI、已安装主机、真实付费 OCR/
Graphify 和生产验收分别报告。[当前操作](unattended-sync.md)、
[发布流程](releasing.md)。
