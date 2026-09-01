# 线索输出表格模板

最终输出（用户要求的 13 字段）：

| 公司名称 | 国家 | 城市 | 官网 | 电话 | 邮箱 | LinkedIn链接 | 客户类型 | 匹配度评分 | 分级 | 开发理由 | Google Maps入口 | 来源URL |
|---|---|---|---|---|---|---|---|---|---|---|---|---|

## 字段填写说明

| 字段 | 来源 |
|------|------|
| 公司名称 | `fetch_gmaps.py` CSV 的 `company_name` |
| 国家 / 城市 | 搜索词里的目标国家/城市（Google Maps 结果已在目标区域） |
| 官网 | CSV 的 `website`（背调后替换黄页伪官网为真实官网） |
| 电话 | CSV 的 `phone` |
| 邮箱 | 背调脚本/Claude 从官网提取 |
| LinkedIn链接 | Claude WebSearch 补 |
| 客户类型 | 背调判断（distributor / wholesaler / installer / retailer） |
| 匹配度评分 | 五维评分总和 |
| 分级 | A / B / C |
| 开发理由 | Claude 基于匹配理由生成（一句） |
| Google Maps入口 | CSV 的 `google_maps_url` |
| 来源URL | 官网 URL + Google Maps 链接 |

## 分级样式

- **A 级**（80-100）：优先跟进 —— 明确经销商 + 我方品类匹配 + 有决策人邮箱
- **B 级**（50-79）：培育跟进 —— 有批发板块但信息待补
- **C 级**（0-49）：暂存 —— 弱匹配或信息不足

## 保存/导出

用户要求保存时，同时提供 Markdown 表格 + CSV 字段（`company_name,country,city,website,phone,email,linkedin,customer_type,match_score,grade,reason,google_maps_url,source_url`）。
