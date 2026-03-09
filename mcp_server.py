#!/usr/bin/env python3
import json
import os
import sys
import traceback
from typing import Any, Dict, Optional

from analysis_core import run_analysis
from data_acquisition import acquire_market_inputs

SERVER_NAME = "market-analysis-mcp"
SERVER_VERSION = "0.1.0"


class MCPServer:
    def __init__(self) -> None:
        self.latest_result: Optional[Dict[str, Any]] = None
        self.latest_inputs: Optional[Dict[str, Any]] = None

    def _send(self, payload: Dict[str, Any]) -> None:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    def _send_response(self, request_id: Any, result: Dict[str, Any]) -> None:
        self._send({"jsonrpc": "2.0", "id": request_id, "result": result})

    def _send_error(self, request_id: Any, code: int, message: str) -> None:
        self._send({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}})

    def _notify(self, level: str, message: str) -> None:
        self._send(
            {
                "jsonrpc": "2.0",
                "method": "notifications/message",
                "params": {"level": level, "data": message},
            }
        )

    def _emit_progress(self, stage: str, message: str) -> None:
        self._send(
            {
                "jsonrpc": "2.0",
                "method": "notifications/progress",
                "params": {"stage": stage, "message": message},
            }
        )

    def handle_initialize(self, request_id: Any) -> None:
        self._send_response(
            request_id,
            {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "capabilities": {"tools": {}, "resources": {}},
            },
        )

    def handle_tools_list(self, request_id: Any) -> None:
        tools = [
            {
                "name": "run_full_analysis",
                "description": "执行商圈创业赛道全链路分析并生成实时汇报事件",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "poi_path": {"type": "string"},
                        "context_path": {"type": "string"},
                        "dictionary_path": {"type": "string"},
                        "center_lat": {"type": "number"},
                        "center_lng": {"type": "number"},
                        "radius_km": {"type": "number", "default": 1.5},
                        "output_dir": {"type": "string"},
                    },
                    "required": [
                        "poi_path",
                        "context_path",
                        "dictionary_path",
                        "center_lat",
                        "center_lng",
                        "output_dir",
                    ],
                },
            },
            {
                "name": "acquire_market_inputs",
                "description": "自动获取 POI/context/center/radius 输入参数，尽量减少人工操作",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "region_query": {"type": "string"},
                        "output_dir": {"type": "string"},
                        "default_radius_km": {"type": "number", "default": 1.5},
                        "countrycodes": {"type": "string"},
                    },
                    "required": ["region_query", "output_dir"],
                },
            },
            {
                "name": "auto_acquire_and_analyze",
                "description": "一键自动取数并完成行业分析",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "region_query": {"type": "string"},
                        "output_dir": {"type": "string"},
                        "dictionary_path": {"type": "string"},
                        "default_radius_km": {"type": "number", "default": 1.5},
                        "countrycodes": {"type": "string"},
                    },
                    "required": ["region_query", "output_dir", "dictionary_path"],
                },
            },
            {
                "name": "get_latest_summary",
                "description": "获取最近一次分析摘要（无需再次计算）",
                "inputSchema": {"type": "object", "properties": {}},
            },
            {
                "name": "health_check",
                "description": "检查MCP服务是否可用",
                "inputSchema": {"type": "object", "properties": {}},
            },
        ]
        self._send_response(request_id, {"tools": tools})

    def _validate_path(self, path: str) -> str:
        if not os.path.exists(path):
            raise ValueError(f"路径不存在: {path}")
        return os.path.abspath(path)

    def _call_run_full_analysis(self, arguments: Dict[str, Any], emit_notifications: bool = True) -> Dict[str, Any]:
        poi_path = self._validate_path(arguments["poi_path"])
        context_path = self._validate_path(arguments["context_path"])
        dictionary_path = self._validate_path(arguments["dictionary_path"])
        output_dir = os.path.abspath(arguments["output_dir"])

        radius_km = float(arguments.get("radius_km", 1.5))
        result = run_analysis(
            poi_path=poi_path,
            context_path=context_path,
            dictionary_path=dictionary_path,
            center_lat=float(arguments["center_lat"]),
            center_lng=float(arguments["center_lng"]),
            radius_km=radius_km,
            output_dir=output_dir,
            emit_progress=self._emit_progress if emit_notifications else None,
        )
        self.latest_result = result

        summary = {
            "coverage": round(result["coverage"] * 100, 2),
            "top3": result["metrics"][:3],
            "robustness": result["robustness"],
            "sensitivity": result["sensitivity"],
            "outputs": result["outputs"],
        }
        return summary

    def _call_acquire_market_inputs(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        output_dir = os.path.abspath(arguments["output_dir"])
        result = acquire_market_inputs(
            region_query=arguments["region_query"],
            output_dir=output_dir,
            default_radius_km=float(arguments.get("default_radius_km", 1.5)),
            countrycodes=arguments.get("countrycodes"),
            emit_progress=self._emit_progress,
        )
        self.latest_inputs = result
        return result

    def handle_tools_call(self, request_id: Any, params: Dict[str, Any]) -> None:
        name = params.get("name")
        arguments = params.get("arguments", {}) or {}

        if name == "health_check":
            result = {"ok": True, "server": SERVER_NAME, "version": SERVER_VERSION}
        elif name == "get_latest_summary":
            if not self.latest_result:
                result = {"available": False, "message": "暂无历史分析结果，请先调用 run_full_analysis"}
            else:
                result = {
                    "available": True,
                    "coverage": round(self.latest_result["coverage"] * 100, 2),
                    "top3": self.latest_result["metrics"][:3],
                    "outputs": self.latest_result["outputs"],
                }
        elif name == "run_full_analysis":
            result = self._call_run_full_analysis(arguments)
        elif name == "acquire_market_inputs":
            result = self._call_acquire_market_inputs(arguments)
        elif name == "auto_acquire_and_analyze":
            acquired = self._call_acquire_market_inputs(arguments)
            analysis_args = {
                "poi_path": acquired["poi_path"],
                "context_path": acquired["context_path"],
                "dictionary_path": arguments["dictionary_path"],
                "center_lat": acquired["center_lat"],
                "center_lng": acquired["center_lng"],
                "radius_km": acquired["radius_km"],
                "output_dir": arguments["output_dir"],
            }
            analyzed = self._call_run_full_analysis(analysis_args)
            result = {"inputs": acquired, "analysis": analyzed}
        else:
            self._send_error(request_id, -32601, f"未知工具: {name}")
            return

        self._send_response(
            request_id,
            {
                "content": [
                    {"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)},
                ],
                "structuredContent": result,
            },
        )

    def handle_resources_list(self, request_id: Any) -> None:
        resources = [
            {
                "uri": "analysis://latest/summary",
                "name": "Latest Analysis Summary",
                "description": "最近一次分析的摘要",
                "mimeType": "application/json",
            },
            {
                "uri": "analysis://latest/inputs",
                "name": "Latest Acquired Inputs",
                "description": "最近一次自动取数结果",
                "mimeType": "application/json",
            },
            {
                "uri": "analysis://latest/scorecard",
                "name": "Latest Industry Scorecard",
                "description": "最近一次行业评分详情",
                "mimeType": "application/json",
            },
            {
                "uri": "analysis://latest/opportunities",
                "name": "Latest Top Opportunities",
                "description": "最近一次Top机会清单",
                "mimeType": "application/json",
            },
        ]
        self._send_response(request_id, {"resources": resources})

    def handle_resources_read(self, request_id: Any, params: Dict[str, Any]) -> None:
        uri = params.get("uri", "")
        if uri == "analysis://latest/inputs":
            if not self.latest_inputs:
                self._send_error(request_id, -32001, "暂无可读取资源，请先执行 acquire_market_inputs")
                return
            data = self.latest_inputs
        elif uri == "analysis://latest/summary":
            if not self.latest_result:
                self._send_error(request_id, -32001, "暂无可读取资源，请先执行 run_full_analysis")
                return
            data = {
                "coverage": self.latest_result["coverage"],
                "top3": self.latest_result["metrics"][:3],
                "outputs": self.latest_result["outputs"],
            }
        elif uri == "analysis://latest/scorecard":
            if not self.latest_result:
                self._send_error(request_id, -32001, "暂无可读取资源，请先执行 run_full_analysis")
                return
            data = self.latest_result["metrics"]
        elif uri == "analysis://latest/opportunities":
            if not self.latest_result:
                self._send_error(request_id, -32001, "暂无可读取资源，请先执行 run_full_analysis")
                return
            data = self.latest_result["top_opportunities"]
        else:
            self._send_error(request_id, -32602, f"未知资源: {uri}")
            return

        self._send_response(
            request_id,
            {
                "contents": [
                    {
                        "uri": uri,
                        "mimeType": "application/json",
                        "text": json.dumps(data, ensure_ascii=False, indent=2),
                    }
                ]
            },
        )

    def handle_message(self, msg: Dict[str, Any]) -> None:
        method = msg.get("method")
        request_id = msg.get("id")
        params = msg.get("params", {})

        try:
            if method == "initialize":
                self.handle_initialize(request_id)
            elif method == "notifications/initialized":
                return
            elif method == "tools/list":
                self.handle_tools_list(request_id)
            elif method == "tools/call":
                self.handle_tools_call(request_id, params)
            elif method == "resources/list":
                self.handle_resources_list(request_id)
            elif method == "resources/read":
                self.handle_resources_read(request_id, params)
            else:
                self._send_error(request_id, -32601, f"未知方法: {method}")
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            self._notify("error", f"调用失败: {exc}")
            self._send_error(request_id, -32000, str(exc))

    def serve(self) -> None:
        self._notify("info", f"{SERVER_NAME} 已启动")
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            self.handle_message(msg)


def main() -> None:
    server = MCPServer()
    server.serve()


if __name__ == "__main__":
    main()
