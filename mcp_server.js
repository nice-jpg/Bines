#!/usr/bin/env node
const fs = require('node:fs');
const path = require('node:path');
const readline = require('node:readline');

const { runAnalysis } = require('./analysis_core');
const { acquireMarketInputs } = require('./data_acquisition');

const SERVER_NAME = 'market-analysis-mcp';
const SERVER_VERSION = '0.1.0';

class MCPServer {
  constructor() {
    this.latestResult = null;
    this.latestInputs = null;
  }

  send(payload) {
    process.stdout.write(`${JSON.stringify(payload, null, 0)}\n`);
  }

  sendResponse(requestId, result) {
    this.send({ jsonrpc: '2.0', id: requestId, result });
  }

  sendError(requestId, code, message) {
    this.send({ jsonrpc: '2.0', id: requestId, error: { code, message } });
  }

  notify(level, message) {
    this.send({ jsonrpc: '2.0', method: 'notifications/message', params: { level, data: message } });
  }

  emitProgress(stage, message) {
    this.send({ jsonrpc: '2.0', method: 'notifications/progress', params: { stage, message } });
  }

  handleInitialize(requestId) {
    this.sendResponse(requestId, {
      protocolVersion: '2024-11-05',
      serverInfo: { name: SERVER_NAME, version: SERVER_VERSION },
      capabilities: { tools: {}, resources: {} },
    });
  }

  handleToolsList(requestId) {
    this.sendResponse(requestId, {
      tools: [
        {
          name: 'run_full_analysis',
          description: '执行商圈创业赛道全链路分析并生成实时汇报事件',
          inputSchema: {
            type: 'object',
            properties: {
              poi_path: { type: 'string' },
              context_path: { type: 'string' },
              dictionary_path: { type: 'string' },
              center_lat: { type: 'number' },
              center_lng: { type: 'number' },
              radius_km: { type: 'number', default: 1.5 },
              output_dir: { type: 'string' },
            },
            required: ['poi_path', 'context_path', 'dictionary_path', 'center_lat', 'center_lng', 'output_dir'],
          },
        },
        {
          name: 'acquire_market_inputs',
          description: '自动获取 POI/context/center/radius 输入参数，尽量减少人工操作',
          inputSchema: {
            type: 'object',
            properties: {
              region_query: { type: 'string' },
              output_dir: { type: 'string' },
              default_radius_km: { type: 'number', default: 1.5 },
              data_source: { type: 'string', enum: ['amap', 'osm'], default: 'amap' },
              amap_key: { type: 'string' },
              countrycodes: { type: 'string' },
            },
            required: ['region_query', 'output_dir'],
          },
        },
        {
          name: 'auto_acquire_and_analyze',
          description: '一键自动取数并完成行业分析',
          inputSchema: {
            type: 'object',
            properties: {
              region_query: { type: 'string' },
              output_dir: { type: 'string' },
              dictionary_path: { type: 'string' },
              default_radius_km: { type: 'number', default: 1.5 },
              data_source: { type: 'string', enum: ['amap', 'osm'], default: 'amap' },
              amap_key: { type: 'string' },
              countrycodes: { type: 'string' },
            },
            required: ['region_query', 'output_dir', 'dictionary_path'],
          },
        },
        {
          name: 'get_latest_summary',
          description: '获取最近一次分析摘要（无需再次计算）',
          inputSchema: { type: 'object', properties: {} },
        },
        {
          name: 'health_check',
          description: '检查MCP服务是否可用',
          inputSchema: { type: 'object', properties: {} },
        },
      ],
    });
  }

  validatePath(filePath) {
    if (!fs.existsSync(filePath)) throw new Error(`路径不存在: ${filePath}`);
    return path.resolve(filePath);
  }

  callRunFullAnalysis(arguments_, emitNotifications = true) {
    const result = runAnalysis({
      poiPath: this.validatePath(arguments_.poi_path),
      contextPath: this.validatePath(arguments_.context_path),
      dictionaryPath: this.validatePath(arguments_.dictionary_path),
      centerLat: Number(arguments_.center_lat),
      centerLng: Number(arguments_.center_lng),
      radiusKm: Number(arguments_.radius_km || 1.5),
      outputDir: path.resolve(arguments_.output_dir),
      emitProgress: emitNotifications ? (stage, message) => this.emitProgress(stage, message) : null,
    });

    this.latestResult = result;
    return {
      coverage: Number((result.coverage * 100).toFixed(2)),
      top3: result.metrics.slice(0, 3),
      robustness: result.robustness,
      sensitivity: result.sensitivity,
      outputs: result.outputs,
    };
  }

  async callAcquireMarketInputs(arguments_) {
    const result = await acquireMarketInputs({
      regionQuery: arguments_.region_query,
      outputDir: path.resolve(arguments_.output_dir),
      defaultRadiusKm: Number(arguments_.default_radius_km || 1.5),
      dataSource: arguments_.data_source || 'amap',
      amapKey: arguments_.amap_key || null,
      countrycodes: arguments_.countrycodes || null,
      emitProgress: (stage, message) => this.emitProgress(stage, message),
    });
    this.latestInputs = result;
    return result;
  }

  async handleToolsCall(requestId, params = {}) {
    const name = params.name;
    const arguments_ = params.arguments || {};
    let result;

    if (name === 'health_check') {
      result = { ok: true, server: SERVER_NAME, version: SERVER_VERSION };
    } else if (name === 'get_latest_summary') {
      if (!this.latestResult) {
        result = { available: false, message: '暂无历史分析结果，请先调用 run_full_analysis' };
      } else {
        result = {
          available: true,
          coverage: Number((this.latestResult.coverage * 100).toFixed(2)),
          top3: this.latestResult.metrics.slice(0, 3),
          outputs: this.latestResult.outputs,
        };
      }
    } else if (name === 'run_full_analysis') {
      result = this.callRunFullAnalysis(arguments_);
    } else if (name === 'acquire_market_inputs') {
      result = await this.callAcquireMarketInputs(arguments_);
    } else if (name === 'auto_acquire_and_analyze') {
      const acquired = await this.callAcquireMarketInputs(arguments_);
      const analyzed = this.callRunFullAnalysis({
        poi_path: acquired.poi_path,
        context_path: acquired.context_path,
        dictionary_path: arguments_.dictionary_path,
        center_lat: acquired.center_lat,
        center_lng: acquired.center_lng,
        radius_km: acquired.radius_km,
        output_dir: arguments_.output_dir,
      });
      result = { inputs: acquired, analysis: analyzed };
    } else {
      this.sendError(requestId, -32601, `未知工具: ${name}`);
      return;
    }

    this.sendResponse(requestId, {
      content: [{ type: 'text', text: JSON.stringify(result, null, 2) }],
      structuredContent: result,
    });
  }

  handleResourcesList(requestId) {
    this.sendResponse(requestId, {
      resources: [
        { uri: 'analysis://latest/summary', name: 'Latest Analysis Summary', description: '最近一次分析的摘要', mimeType: 'application/json' },
        { uri: 'analysis://latest/inputs', name: 'Latest Acquired Inputs', description: '最近一次自动取数结果', mimeType: 'application/json' },
        { uri: 'analysis://latest/scorecard', name: 'Latest Industry Scorecard', description: '最近一次行业评分详情', mimeType: 'application/json' },
        { uri: 'analysis://latest/opportunities', name: 'Latest Top Opportunities', description: '最近一次Top机会清单', mimeType: 'application/json' },
      ],
    });
  }

  handleResourcesRead(requestId, params = {}) {
    const uri = params.uri || '';
    let data;

    if (uri === 'analysis://latest/inputs') {
      if (!this.latestInputs) return this.sendError(requestId, -32001, '暂无可读取资源，请先执行 acquire_market_inputs');
      data = this.latestInputs;
    } else if (uri === 'analysis://latest/summary') {
      if (!this.latestResult) return this.sendError(requestId, -32001, '暂无可读取资源，请先执行 run_full_analysis');
      data = { coverage: this.latestResult.coverage, top3: this.latestResult.metrics.slice(0, 3), outputs: this.latestResult.outputs };
    } else if (uri === 'analysis://latest/scorecard') {
      if (!this.latestResult) return this.sendError(requestId, -32001, '暂无可读取资源，请先执行 run_full_analysis');
      data = this.latestResult.metrics;
    } else if (uri === 'analysis://latest/opportunities') {
      if (!this.latestResult) return this.sendError(requestId, -32001, '暂无可读取资源，请先执行 run_full_analysis');
      data = this.latestResult.top_opportunities;
    } else {
      return this.sendError(requestId, -32602, `未知资源: ${uri}`);
    }

    this.sendResponse(requestId, {
      contents: [{ uri, mimeType: 'application/json', text: JSON.stringify(data, null, 2) }],
    });
  }

  async handleMessage(msg) {
    const method = msg.method;
    const requestId = msg.id;
    const params = msg.params || {};

    try {
      if (method === 'initialize') this.handleInitialize(requestId);
      else if (method === 'notifications/initialized') return;
      else if (method === 'tools/list') this.handleToolsList(requestId);
      else if (method === 'tools/call') await this.handleToolsCall(requestId, params);
      else if (method === 'resources/list') this.handleResourcesList(requestId);
      else if (method === 'resources/read') this.handleResourcesRead(requestId, params);
      else this.sendError(requestId, -32601, `未知方法: ${method}`);
    } catch (err) {
      this.notify('error', `调用失败: ${err.message || String(err)}`);
      this.sendError(requestId, -32000, err.message || String(err));
    }
  }

  async serve() {
    this.notify('info', `${SERVER_NAME} 已启动`);
    const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
    for await (const line of rl) {
      const txt = line.trim();
      if (!txt) continue;
      let msg;
      try {
        msg = JSON.parse(txt);
      } catch {
        continue;
      }
      await this.handleMessage(msg);
    }
  }
}

async function main() {
  const server = new MCPServer();
  await server.serve();
}

if (require.main === module) {
  main().catch((err) => {
    process.stderr.write(`${err.message || String(err)}\n`);
    process.exit(1);
  });
}

module.exports = { MCPServer };
