const fs = require('node:fs');
const path = require('node:path');

const INDUSTRIES = ['餐饮', '零售', '生活服务', '教育培训', '健康服务', '文娱'];

const DEFAULT_WEIGHTS = {
  demand_score: 30.0,
  competition_score: 25.0,
  unit_economics_score: 30.0,
  stability_score: 15.0,
};

const INDUSTRY_PROFILES = {
  餐饮: { time_signal: 82, repurchase: 75, gross_margin_feasibility: 65, seasonality: 40, policy_sensitivity: 35, supply_chain_complexity: 65, space_intensity: 70, payback_months: [10, 18], first_store_model: '轻正餐/快餐单店', budget_level: '中' },
  零售: { time_signal: 70, repurchase: 60, gross_margin_feasibility: 60, seasonality: 55, policy_sensitivity: 30, supply_chain_complexity: 55, space_intensity: 55, payback_months: [12, 24], first_store_model: '高周转小店', budget_level: '中' },
  生活服务: { time_signal: 68, repurchase: 85, gross_margin_feasibility: 70, seasonality: 30, policy_sensitivity: 20, supply_chain_complexity: 35, space_intensity: 45, payback_months: [8, 14], first_store_model: '预约制服务门店', budget_level: '中低' },
  教育培训: { time_signal: 62, repurchase: 65, gross_margin_feasibility: 72, seasonality: 75, policy_sensitivity: 80, supply_chain_complexity: 25, space_intensity: 60, payback_months: [14, 28], first_store_model: '小班课/工作坊', budget_level: '中高' },
  健康服务: { time_signal: 66, repurchase: 70, gross_margin_feasibility: 75, seasonality: 25, policy_sensitivity: 65, supply_chain_complexity: 40, space_intensity: 50, payback_months: [12, 22], first_store_model: '轻医疗/康复咨询', budget_level: '中高' },
  文娱: { time_signal: 74, repurchase: 55, gross_margin_feasibility: 58, seasonality: 60, policy_sensitivity: 40, supply_chain_complexity: 45, space_intensity: 65, payback_months: [12, 24], first_store_model: '主题体验小馆', budget_level: '中' },
};

function round2(v) {
  return Math.round(v * 100) / 100;
}

function clamp(value, low = 0, high = 100) {
  return Math.max(low, Math.min(high, value));
}

function haversineKm(lat1, lon1, lat2, lon2) {
  const r = 6371.0;
  const p1 = (lat1 * Math.PI) / 180;
  const p2 = (lat2 * Math.PI) / 180;
  const dphi = ((lat2 - lat1) * Math.PI) / 180;
  const dlambda = ((lon2 - lon1) * Math.PI) / 180;
  const a = Math.sin(dphi / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dlambda / 2) ** 2;
  return 2 * r * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

function normalize(values, higherIsBetter = true) {
  const keys = Object.keys(values);
  if (!keys.length) return {};
  const vals = keys.map((k) => values[k]);
  const minV = Math.min(...vals);
  const maxV = Math.max(...vals);
  if (maxV === minV) {
    return Object.fromEntries(keys.map((k) => [k, 50.0]));
  }
  const out = {};
  for (const k of keys) {
    let scaled = ((values[k] - minV) / (maxV - minV)) * 100.0;
    if (!higherIsBetter) scaled = 100.0 - scaled;
    out[k] = clamp(scaled);
  }
  return out;
}

function parseCsv(content) {
  const lines = content.trim().split(/\r?\n/);
  if (!lines.length) return [];
  const headers = lines[0].split(',');
  return lines.slice(1).filter(Boolean).map((line) => {
    const cols = line.split(',');
    const row = {};
    headers.forEach((h, i) => {
      row[h] = (cols[i] ?? '').trim();
    });
    return row;
  });
}

function toCsv(headers, rows) {
  const out = [headers.join(',')];
  for (const row of rows) {
    out.push(headers.map((h) => String(row[h] ?? '')).join(','));
  }
  return `${out.join('\n')}\n`;
}

function loadDictionary(filePath) {
  return JSON.parse(fs.readFileSync(filePath, 'utf8')).category_to_l1;
}

function loadRegionContext(filePath) {
  return JSON.parse(fs.readFileSync(filePath, 'utf8'));
}

function loadPoi(filePath) {
  return parseCsv(fs.readFileSync(filePath, 'utf8')).map((r) => ({
    name: r.name,
    category_l2: r.category_l2,
    lat: Number(r.lat),
    lng: Number(r.lng),
    rating: Number(r.rating),
    review_count: Number(r.review_count),
    price_band: Number(r.price_band),
    open_status: r.open_status,
  }));
}

function deduplicatePoi(poiRows) {
  const best = new Map();
  for (const row of poiRows) {
    const key = `${row.name.toLowerCase()}|${row.category_l2.toLowerCase()}|${row.lat.toFixed(4)}|${row.lng.toFixed(4)}`;
    const cur = best.get(key);
    if (!cur || row.review_count > cur.review_count || (row.review_count === cur.review_count && row.rating > cur.rating)) {
      best.set(key, row);
    }
  }
  return [...best.values()];
}

function filterRegion(poiRows, centerLat, centerLng, radiusKm) {
  return poiRows.filter((row) => haversineKm(centerLat, centerLng, row.lat, row.lng) <= radiusKm);
}

function assignIndustry(poiRows, categoryMap) {
  return poiRows.map((row) => ({ ...row, industry: categoryMap[row.category_l2] || '生活服务' }));
}

function suspiciousOutlier(row) {
  return row.rating >= 4.9 && row.review_count <= 3;
}

function computeScores(poiRows, regionContext, weights = DEFAULT_WEIGHTS) {
  const area = Math.PI;
  const byIndustry = Object.fromEntries(INDUSTRIES.map((x) => [x, []]));
  for (const row of poiRows) {
    if (!byIndustry[row.industry]) byIndustry[row.industry] = [];
    byIndustry[row.industry].push(row);
  }

  const countRaw = {};
  const reviewDensityRaw = {};
  const ratingStabilityRaw = {};
  const hhiRaw = {};
  const survivalRaw = {};
  const priceFitRaw = {};

  const rentProxy = Number(regionContext.rent_proxy);
  const officeResRatio = Number(regionContext.office_residential_ratio);
  const popProxy = Number(regionContext.day_night_population_proxy);
  const accessibility = Number(regionContext.accessibility_proxy);

  for (const industry of INDUSTRIES) {
    const rows = byIndustry[industry] || [];
    if (!rows.length) {
      countRaw[industry] = 0;
      reviewDensityRaw[industry] = 0;
      ratingStabilityRaw[industry] = 0;
      hhiRaw[industry] = 1;
      survivalRaw[industry] = 0;
      priceFitRaw[industry] = 0;
      continue;
    }

    const openRows = rows.filter((r) => r.open_status === 'open');
    countRaw[industry] = openRows.length / area;
    reviewDensityRaw[industry] = openRows.reduce((s, r) => s + Math.log1p(r.review_count), 0) / area;
    const weighted = openRows.reduce((s, r) => s + r.rating * Math.log1p(r.review_count), 0);
    const weightTotal = Math.max(1.0, openRows.reduce((s, r) => s + Math.log1p(r.review_count), 0));
    ratingStabilityRaw[industry] = weighted / weightTotal;

    const chain = {};
    for (const r of openRows) {
      const name = r.name.split(' ')[0].toLowerCase();
      chain[name] = (chain[name] || 0) + 1;
    }
    const totalOpen = Math.max(1, openRows.length);
    hhiRaw[industry] = Object.values(chain).reduce((s, cnt) => s + (cnt / totalOpen) ** 2, 0);

    const mature = openRows.filter((r) => r.review_count >= 20 && r.rating >= 4.0);
    survivalRaw[industry] = mature.length / totalOpen;

    const priceVals = openRows.map((r) => r.price_band).sort((a, b) => a - b);
    const medianPrice = priceVals[Math.floor(priceVals.length / 2)] ?? 2;
    priceFitRaw[industry] = 100 - Math.min(100, Math.abs(medianPrice - 2) * 35);
  }

  const countScore = normalize(countRaw, true);
  const reviewScore = normalize(reviewDensityRaw, true);
  const ratingScore = normalize(ratingStabilityRaw, true);
  const densityInverse = normalize(countRaw, false);
  const hhiInverse = normalize(hhiRaw, false);
  const survivalScore = normalize(survivalRaw, true);

  const metrics = [];
  for (const industry of INDUSTRIES) {
    const p = INDUSTRY_PROFILES[industry];
    const demand =
      0.35 * countScore[industry] +
      0.3 * reviewScore[industry] +
      0.2 * ratingScore[industry] +
      0.15 * clamp((p.time_signal + 0.3 * accessibility + 0.2 * popProxy) / 1.5);

    const competition = 0.45 * densityInverse[industry] + 0.3 * hhiInverse[industry] + 0.25 * survivalScore[industry];

    const rentPenalty = (rentProxy * p.space_intensity) / 100;
    const officeResBoost = clamp(50 + (officeResRatio - 1.0) * 20);
    const unitEcon =
      0.3 * priceFitRaw[industry] +
      0.25 * p.repurchase +
      0.25 * p.gross_margin_feasibility +
      0.2 * clamp((officeResBoost + 100 - rentPenalty) / 2);

    const stability =
      0.4 * (100 - p.seasonality) + 0.35 * (100 - p.policy_sensitivity) + 0.25 * (100 - p.supply_chain_complexity);

    const total =
      demand * (weights.demand_score / 100) +
      competition * (weights.competition_score / 100) +
      unitEcon * (weights.unit_economics_score / 100) +
      stability * (weights.stability_score / 100);

    const totalScore = round2(total);
    let recommendation = '暂缓';
    if (totalScore >= 75) recommendation = '优先进入';
    else if (totalScore >= 60) recommendation = '小规模验证';

    metrics.push({
      industry,
      demand_score: round2(demand),
      competition_score: round2(competition),
      unit_economics_score: round2(unitEcon),
      stability_score: round2(stability),
      total_score: totalScore,
      recommendation,
    });
  }

  return metrics.sort((a, b) => b.total_score - a.total_score);
}

function coverageRatio(poiRows) {
  const covered = new Set(poiRows.map((r) => r.industry));
  return covered.size / INDUSTRIES.length;
}

function buildOpportunityItem(metric) {
  const p = INDUSTRY_PROFILES[metric.industry];
  const opp = [];
  const risk = [];
  if (metric.demand_score >= 65) opp.push('需求强度较高，可快速形成首店客流');
  if (metric.unit_economics_score >= 65) opp.push('单店经济性较优，现金回收周期可控');
  if (metric.competition_score >= 60) opp.push('竞争压力相对可承受，存在切入窗口');
  if (metric.stability_score < 55) risk.push('经营稳定性偏弱，需控制季节性与政策波动');
  if (metric.competition_score < 50) risk.push('竞争较拥挤，需要差异化定位');
  if (metric.unit_economics_score < 55) risk.push('单店模型脆弱，需先验证成本结构');
  if (!opp.length) opp.push('存在细分定位机会，建议小样本验证');
  if (!risk.length) risk.push('主要风险可控，重点关注执行效率');

  return {
    industry: metric.industry,
    total_score: metric.total_score,
    recommendation: metric.recommendation,
    opportunity_points: opp,
    risk_points: risk,
    validation_actions: [
      '连续7天分时段客流抽样（工作日+周末）',
      '电话访谈10家同类门店，确认租金与人效区间',
      '进行2周最小化产品测试，验证复购与毛利',
    ],
    estimated_payback_months: `${p.payback_months[0]}-${p.payback_months[1]}个月`,
    first_store_model: p.first_store_model,
    budget_level: p.budget_level,
    stop_loss_condition: '连续2个月毛利率低于目标线且复购不达预期则止损',
  };
}

function writeScorecard(filePath, metrics) {
  const headers = ['industry', 'demand_score', 'competition_score', 'unit_economics_score', 'stability_score', 'total_score', 'recommendation'];
  fs.writeFileSync(filePath, toCsv(headers, metrics), 'utf8');
}

function writeOpportunities(filePath, metrics, topN = 3) {
  fs.writeFileSync(filePath, JSON.stringify(metrics.slice(0, topN).map(buildOpportunityItem), null, 2), 'utf8');
}

function runWeightSensitivity(poiRows, regionContext) {
  const baseline = computeScores(poiRows, regionContext, DEFAULT_WEIGHTS);
  const baseTop3 = baseline.slice(0, 3).map((m) => m.industry);
  const stability = {};

  for (const key of Object.keys(DEFAULT_WEIGHTS)) {
    for (const factor of [0.8, 1.2]) {
      const modified = { ...DEFAULT_WEIGHTS, [key]: DEFAULT_WEIGHTS[key] * factor };
      const total = Object.values(modified).reduce((s, v) => s + v, 0);
      for (const k of Object.keys(modified)) modified[k] = (modified[k] / total) * 100;
      const current = computeScores(poiRows, regionContext, modified).slice(0, 3).map((m) => m.industry);
      stability[`${key}_${factor.toFixed(1)}`] = new Set([...baseTop3.filter((x) => current.includes(x))]).size;
    }
  }
  return { baseline_top3: baseTop3, overlap_with_variants: stability };
}

function runOutlierRobustness(poiRows, regionContext) {
  const baseline = computeScores(poiRows, regionContext, DEFAULT_WEIGHTS);
  const cleanRows = poiRows.filter((r) => !suspiciousOutlier(r));
  const cleaned = computeScores(cleanRows, regionContext, DEFAULT_WEIGHTS);
  const b = baseline.slice(0, 3).map((m) => m.industry);
  const c = cleaned.slice(0, 3).map((m) => m.industry);
  return {
    baseline_top3: b,
    cleaned_top3: c,
    removed_outliers: poiRows.length - cleanRows.length,
    overlap: b.filter((x) => c.includes(x)).length,
  };
}

function writeReport(filePath, metrics, coverage, sensitivity, robustness) {
  const top = metrics.slice(0, 3);
  const lines = [];
  lines.push('# 商圈创业赛道分析报告（MVP）');
  lines.push('');
  lines.push('## 结论摘要');
  lines.push('');
  lines.push('- 目标：确定商圈内可优先验证的创业赛道。');
  lines.push('- 风险偏好：稳健现金流。');
  lines.push('- 推荐优先级（Top 3）：');
  top.forEach((m, i) => lines.push(`  - ${i + 1}. ${m.industry}（${m.total_score}分，${m.recommendation}）`));
  lines.push('');
  lines.push('## 评分结果');
  lines.push('');
  metrics.forEach((m) => lines.push(`- ${m.industry}: 总分${m.total_score} | 需求${m.demand_score} | 竞争${m.competition_score} | 单店经济性${m.unit_economics_score} | 稳定性${m.stability_score} | 结论${m.recommendation}`));
  lines.push('');
  lines.push('## 测试与稳健性');
  lines.push('');
  lines.push(`- 行业覆盖率：${(coverage * 100).toFixed(1)}%（目标>=80%）`);
  lines.push(`- 权重敏感性（Top3重合度）：${JSON.stringify(sensitivity.overlap_with_variants)}`);
  lines.push(`- 异常值鲁棒性：剔除${robustness.removed_outliers}条可疑样本后，Top3重合${robustness.overlap}/3`);
  lines.push('');
  lines.push('## 90天验证建议');
  lines.push('');
  lines.push('- 前2周：完成客流与转化抽样，验证真实需求。');
  lines.push('- 第3-6周：跑最小化门店模型，确认毛利与复购。');
  lines.push('- 第7-12周：扩展到第二个点位，验证可复制性。');
  fs.writeFileSync(filePath, `${lines.join('\n')}\n`, 'utf8');
}

function runAnalysis({ poiPath, contextPath, dictionaryPath, centerLat, centerLng, radiusKm, outputDir, emitProgress }) {
  if (emitProgress) emitProgress('data_loading', '开始加载输入数据');
  const categoryMap = loadDictionary(dictionaryPath);
  const regionContext = loadRegionContext(contextPath);
  let poiRows = loadPoi(poiPath);

  if (emitProgress) emitProgress('data_cleaning', '执行去重、地理过滤与行业归类');
  poiRows = assignIndustry(filterRegion(deduplicatePoi(poiRows), centerLat, centerLng, radiusKm), categoryMap);

  if (emitProgress) emitProgress('scoring', '计算行业评分与结论');
  const metrics = computeScores(poiRows, regionContext, DEFAULT_WEIGHTS);
  const coverage = coverageRatio(poiRows);

  if (emitProgress) emitProgress('validation', '执行敏感性与鲁棒性检查');
  const sensitivity = runWeightSensitivity(poiRows, regionContext);
  const robustness = runOutlierRobustness(poiRows, regionContext);

  let outputs = {};
  if (outputDir) {
    fs.mkdirSync(outputDir, { recursive: true });
    const scorecardPath = path.join(outputDir, 'industry_scorecard.csv');
    const opportunitiesPath = path.join(outputDir, 'top_opportunities.json');
    const reportPath = path.join(outputDir, 'analysis_report.md');
    writeScorecard(scorecardPath, metrics);
    writeOpportunities(opportunitiesPath, metrics, 3);
    writeReport(reportPath, metrics, coverage, sensitivity, robustness);
    outputs = { industry_scorecard: scorecardPath, top_opportunities: opportunitiesPath, analysis_report: reportPath };
  }

  const result = {
    coverage,
    metrics,
    top_opportunities: metrics.slice(0, 3).map(buildOpportunityItem),
    sensitivity,
    robustness,
    outputs,
  };

  if (emitProgress) emitProgress('done', '分析完成');
  return result;
}

module.exports = {
  INDUSTRIES,
  DEFAULT_WEIGHTS,
  clamp,
  haversineKm,
  normalize,
  loadDictionary,
  loadRegionContext,
  loadPoi,
  deduplicatePoi,
  filterRegion,
  assignIndustry,
  suspiciousOutlier,
  computeScores,
  coverageRatio,
  buildOpportunityItem,
  writeScorecard,
  writeOpportunities,
  runWeightSensitivity,
  runOutlierRobustness,
  writeReport,
  runAnalysis,
};
