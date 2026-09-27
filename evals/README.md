# 评测集

用一批已知标准答案的片名，给搜索结果打分，防止改动把「搜得准不准」弄坏。

## 三种跑法（都在仓库根目录执行）

```powershell
# 1. 易错样例（默认，CI 跑的就是这个）：手工构造的搜索结果，不联网、不要 key
python -m evals.run --synthetic

# 2. 连真实服务跑：读你本地 .env 里的 key，真实搜索 + 验证，同时录制到 evals/recordings/
python -m evals.run --live                                  # 全部 51 个片名
python -m evals.run --live --only guichuideng,jingyinglvshi # 只跑几个
python -m evals.run --live --limit 5                        # 先跑前 5 个试试
python -m evals.run --live --no-record                      # 只看分数，不录制

# 3. 回放录制：完全离线，重放 evals/recordings/ 里的真实响应（CI 也会跑）
python -m evals.run --replay
```

报告写到 `evals/reports/<模式>-latest.md` 和 `.json`（不入库）。

## 指标

| 指标 | 含义 |
|---|---|
| 相关率 precision | 判为相关的有效链接里，真是那部片的比例（越高越不会给错片） |
| 召回率 recall | 真是那部片的有效链接里，被判为相关的比例 |
| 有效率 validity | 验证出结果的链接里，还有效的比例 |
| 首条准确率 top1 | 排第一的相关结果是对的比例 |
| 命中率 found | 至少找到一个真相关有效链接的片名占比 |

## 文件

- `titles.json`：51 个片名的标准答案（片名、别名、年份、类型、季），含鬼吹灯、精英律师这类易错例子。
- `synthetic.json`：易错样例的构造结果（聚合页、演员名、续集和年份混淆、英文分享名等）。
- `labels.json`（可选）：裁判判错时人工改判，格式 `{"片名id": {"分享链接": true/false}}`。
- `recordings/`：`--live` 录下的真实响应，提交后 CI 用 `--replay` 回放当回归检查。

## 录制文件里没有密钥

录制只存响应内容和「方法 + 地址 + 请求体摘要」，**不存任何请求头**（cookie、Authorization 都在头里），
地址参数和 JSON 请求体里的 key / token / cookie 等字段在算摘要前就去掉了。回放时用占位 key，
真实 key 不会出现在录制文件里。提交录制前也可以自己搜一下确认：

```powershell
Select-String -Path evals\recordings\*.json -Pattern "你的key前几位"
```
