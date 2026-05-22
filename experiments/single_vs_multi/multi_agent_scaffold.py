"""Toolathlon용 일반 목적 멀티에이전트 scaffold.

이 모듈은 공식 Toolathlon의 TaskAgent를 최소 침습 방식으로 확장한다.
핵심 실행 루프, MCP 연결, workspace 초기화, 평가 로그 저장은 원본 구현을
그대로 사용하고, Agent 구성을 단일 Assistant에서 공통 6-agent 구조로
바꾸는 데 집중한다.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from agents import Agent, ItemHelpers, ModelSettings, RunConfig, RunContextWrapper, Runner, ToolCallItem, function_tool
from agents.exceptions import MaxTurnsExceeded
from agents.mcp import MCPServer

from utils.roles.task_agent import TaskAgent, local_tool_mappings


PROMPT_FILES: Dict[str, str] = {
    "orchestrator": "orchestrator.md",
    "dynamic_orchestrator": "dynamic_orchestrator.md",
    "research": "research_inspection.md",
    "planning": "planning.md",
    "action": "action_execution.md",
    "verification": "verification.md",
    "memory": "memory_summary.md",
    "specialist_academic_reference": "specialist_academic_reference.md",
    "specialist_document": "specialist_document.md",
    "specialist_ecommerce": "specialist_ecommerce.md",
    "specialist_file_terminal": "specialist_file_terminal.md",
    "specialist_k8s_browser": "specialist_k8s_browser.md",
    "specialist_privacy": "specialist_privacy.md",
    "specialist_spreadsheet": "specialist_spreadsheet.md",
    "specialist_verifier": "specialist_verifier.md",
}


class FilteredMCPServerProxy(MCPServer):
    """Restrict a connected MCP server to a role-specific public tool surface."""

    def __init__(
        self,
        base_server: MCPServer,
        *,
        allowed_tool_names: Optional[Sequence[str]] = None,
        denied_name_patterns: Optional[Sequence[str]] = None,
    ):
        self.base_server = base_server
        self.allowed_tool_names = set(allowed_tool_names or [])
        self.denied_name_patterns = [re.compile(pattern, re.IGNORECASE) for pattern in denied_name_patterns or []]

    @property
    def name(self) -> str:
        return self.base_server.name

    async def connect(self):
        return None

    async def cleanup(self):
        return None

    def _allowed(self, tool_name: str) -> bool:
        if self.allowed_tool_names and tool_name not in self.allowed_tool_names:
            return False
        return not any(pattern.search(tool_name) for pattern in self.denied_name_patterns)

    async def list_tools(self):
        tools = await self.base_server.list_tools()
        return [tool for tool in tools if self._allowed(tool.name)]

    async def call_tool(self, tool_name: str, arguments: dict[str, Any] | None):
        if not self._allowed(tool_name):
            raise PermissionError(f"MCP tool `{self.name}-{tool_name}` is not exposed to this specialist role.")
        return await self.base_server.call_tool(tool_name, arguments)


class MultiAgentTaskAgent(TaskAgent):
    """공식 TaskAgent 실행 루프를 재사용하는 orchestrator-worker agent."""

    def __init__(
        self,
        *args,
        prompt_dir: str | Path | None = None,
        architecture: str = "multi_workflow",
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.prompt_dir = Path(prompt_dir) if prompt_dir else Path(__file__).parent / "prompts"
        self.architecture = architecture
        self.specialist_agents: Dict[str, Agent] = {}
        self.dynamic_profile: Dict[str, Any] = {}
        self.dynamic_specialist_call_counts: Dict[str, int] = {}

    def _read_prompt(self, prompt_key: str) -> str:
        path = self.prompt_dir / PROMPT_FILES[prompt_key]
        return path.read_text(encoding="utf-8")

    def _compose_prompt(self, prompt_key: str) -> str:
        base_prompt = self._read_prompt(prompt_key).strip()
        task_prompt = self.task_config.system_prompts.agent or ""
        fairness_note = """

공통 실험 제약:
- 같은 Toolathlon task_config, 같은 모델, 같은 도구 집합을 사용한다.
- task-specific 정답, 산출물 본문, 셀 좌표, 파일 매핑, reference 매핑을 코드나 prompt에 넣지 않는다.
- 평가 스크립트나 정답 상태를 변경하지 않는다.
- specialist agent는 완료 선언 도구를 받지 않으며, 최종 `claim_done`은 Orchestrator만 호출할 수 있다.
- dynamic supervisor에서는 공개 task metadata와 필요한 도구 종류에 따라 일반 목적 specialist를 선택한다.

원본 Toolathlon 작업 시스템 프롬프트:
"""
        return f"{base_prompt}{fairness_note}{task_prompt}"

    def _build_local_tools(self) -> List[object]:
        local_tools: List[object] = []
        if self.task_config.needed_local_tools is None:
            return local_tools

        for tool_name in self.task_config.needed_local_tools:
            if (
                self.agent_config.model.provider == "openai_stateful_responses"
                and tool_name == "manage_context"
            ):
                continue
            tool_or_toolsets = local_tool_mappings[tool_name]
            if isinstance(tool_or_toolsets, list):
                local_tools.extend(tool_or_toolsets)
            else:
                local_tools.append(tool_or_toolsets)
        return local_tools

    @staticmethod
    def _without_claim_done(tools: Iterable[object]) -> List[object]:
        """Return tools specialists may use; final completion is orchestrator-only."""
        return [tool for tool in tools if getattr(tool, "name", "") != "local-claim_done"]

    def _local_tools_for_names(self, allowed_names: Iterable[str]) -> List[object]:
        allowed = set(allowed_names)
        return [tool for tool in self._build_local_tools() if getattr(tool, "name", "") in allowed]

    def _model_settings(self) -> ModelSettings:
        generation_kwargs = {
            key: getattr(self.agent_config.generation, key)
            for key in vars(self.agent_config.generation)
        }
        return ModelSettings(
            tool_choice=self.agent_config.tool.tool_choice,
            parallel_tool_calls=self.agent_config.tool.parallel_tool_calls,
            **generation_kwargs,
        )

    def _model(self):
        return self.agent_model_provider.get_model(
            self.agent_config.model.real_name,
            debug=self.debug,
            short_model_name=self.agent_config.model.short_name,
        )

    def _agent_kwargs(
        self,
        prompt_key: str,
        tools: Iterable[object],
        *,
        mcp_servers: Optional[Iterable[MCPServer]] = None,
    ) -> dict:
        return {
            "instructions": self._compose_prompt(prompt_key),
            "model": self._model(),
            "mcp_servers": [*(mcp_servers if mcp_servers is not None else self.mcp_manager.get_all_connected_servers())],
            "tools": list(tools),
            "hooks": self.agent_hooks,
            "model_settings": self._model_settings(),
        }

    def _connected_server_by_name(self) -> Dict[str, MCPServer]:
        return dict(getattr(self.mcp_manager, "connected_servers", {}) or {})

    def _filtered_mcp_servers(
        self,
        server_names: Iterable[str],
        *,
        deny_mutations: bool = False,
    ) -> List[MCPServer]:
        connected = self._connected_server_by_name()
        denied = [
            r"(create|update|delete|remove|write|move|copy|rename|patch|apply|submit|send|upload|insert)",
            r"(execute|run|command|shell|bash)",
            r"(batch_update|replace|edit|modify)",
        ] if deny_mutations else []
        servers: List[MCPServer] = []
        for name in server_names:
            if name in connected:
                servers.append(FilteredMCPServerProxy(connected[name], denied_name_patterns=denied))
        return servers

    def _task_profile_text(self) -> str:
        return f"{self.task_config.task_dir}\n{self.task_config.task_str or ''}".lower()

    def _select_dynamic_specialists(self) -> Dict[str, Dict[str, Any]]:
        needed_mcp = set(self.task_config.needed_mcp_servers or [])
        profile_text = self._task_profile_text()

        roster: Dict[str, Dict[str, Any]] = {
            "research": {
                "prompt": "research",
                "mcp": sorted(needed_mcp - {"terminal"}),
                "local": ["local-search_history", "local-view_history_turn", "local-browse_history"],
                "read_only": True,
                "description": "Inspect public task requirements, available state, files, documents, and platform resources before action.",
            },
            "planner": {
                "prompt": "planning",
                "mcp": [],
                "local": [],
                "read_only": True,
                "description": "Convert findings into a concise checklist, risks, and candidate delegation plan.",
            },
            "verifier": {
                "prompt": "specialist_verifier",
                "mcp": sorted(needed_mcp - {"terminal"}),
                "local": ["local-search_history", "local-view_history_turn", "local-browse_history"],
                "read_only": True,
                "description": "Independently verify current public workspace/API state and return PASS, FAIL, or UNSURE.",
            },
            "memory": {
                "prompt": "memory",
                "mcp": [],
                "local": ["local-search_history", "local-view_history_turn", "local-browse_history"],
                "read_only": True,
                "description": "Compress long tool evidence and unresolved issues without adding new facts.",
            },
        }

        if "woocommerce" in needed_mcp:
            roster["ecommerce"] = {
                "prompt": "specialist_ecommerce",
                "mcp": ["woocommerce", *sorted(needed_mcp & {"filesystem"})],
                "local": ["local-python-execute", "local-search_overlong_tooloutput", "local-view_overlong_tooloutput"],
                "read_only": False,
                "description": "Handle ecommerce catalog, inventory, product media, order, and store-report subtasks.",
            }
        if needed_mcp & {"k8s", "playwright_with_chunk"}:
            roster["k8s_browser"] = {
                "prompt": "specialist_k8s_browser",
                "mcp": [name for name in ("k8s", "playwright_with_chunk", "filesystem", "terminal") if name in needed_mcp],
                "local": ["local-search_overlong_tooloutput", "local-view_overlong_tooloutput"],
                "read_only": False,
                "description": "Handle Kubernetes preview inspection and browser QA subtasks.",
            }
        if "excel" in needed_mcp:
            roster["spreadsheet"] = {
                "prompt": "specialist_spreadsheet",
                "mcp": [name for name in ("excel", "filesystem", "terminal") if name in needed_mcp],
                "local": ["local-python-execute", "local-search_overlong_tooloutput", "local-view_overlong_tooloutput"],
                "read_only": False,
                "description": "Handle workbook inspection, data transformation, formula/cell updates, and spreadsheet verification.",
            }
        if needed_mcp & {"pdf-tools", "pptx"}:
            roster["document"] = {
                "prompt": "specialist_document",
                "mcp": [name for name in ("pdf-tools", "pptx", "filesystem") if name in needed_mcp],
                "local": ["local-python-execute", "local-search_overlong_tooloutput", "local-view_overlong_tooloutput"],
                "read_only": False,
                "description": "Handle PDF, slide, and document extraction, comparison, and required document artifacts.",
            }
        if needed_mcp & {"filesystem", "terminal"}:
            roster["file_terminal"] = {
                "prompt": "specialist_file_terminal",
                "mcp": [name for name in ("filesystem", "terminal") if name in needed_mcp],
                "local": ["local-python-execute", "local-search_overlong_tooloutput", "local-view_overlong_tooloutput"],
                "read_only": False,
                "description": "Handle file organization, file creation, shell-assisted inspection, and local artifact checks.",
            }
        if any(term in profile_text for term in ("privacy", "desensit", "redact", "pii", "personal information")):
            roster["privacy"] = {
                "prompt": "specialist_privacy",
                "mcp": [name for name in ("filesystem", "terminal") if name in needed_mcp],
                "local": ["local-python-execute", "local-search_overlong_tooloutput", "local-view_overlong_tooloutput"],
                "read_only": False,
                "description": "Handle PII detection, redaction, desensitization, and privacy-focused verification.",
            }
        if any(term in profile_text for term in ("paper", "reference", "citation", "latex", "bib", "academic")):
            roster["academic_reference"] = {
                "prompt": "specialist_academic_reference",
                "mcp": [name for name in ("filesystem", "terminal", "pdf-tools") if name in needed_mcp],
                "local": ["local-python-execute", "local-search_overlong_tooloutput", "local-view_overlong_tooloutput"],
                "read_only": False,
                "description": "Handle scholarly document/reference consistency and citation repair using public files only.",
            }

        return roster

    def _profile_selection_payload(self, roster: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        return {
            "architecture": "multi_dynamic_supervisor",
            "task_id": self.task_config.task_dir,
            "selection_basis": {
                "needed_mcp_servers": self.task_config.needed_mcp_servers or [],
                "needed_local_tools": self.task_config.needed_local_tools or [],
                "public_task_text_chars": len(self.task_config.task_str or ""),
            },
            "selected_specialists": {
                name: {
                    "prompt": spec["prompt"],
                    "mcp_servers": spec["mcp"],
                    "local_tools": spec["local"],
                    "read_only": spec["read_only"],
                    "description": spec["description"],
                }
                for name, spec in roster.items()
            },
            "fairness_constraints": [
                "No task-specific hardcoded answers or deterministic repair.",
                "No evaluation, groundtruth, answer dump, or previous successful trace access.",
                "Routing is based only on public task text and task_config tool metadata.",
                "Specialists cannot call claim_done.",
            ],
        }

    def _write_profile_selection(self, payload: Dict[str, Any]) -> None:
        workspace = Path(self.task_config.agent_workspace)
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "profile_selection.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _specialist_as_limited_tool(self, name: str, agent: Agent, description: str):
        tool_name = f"consult_{name}"
        max_calls = int(os.getenv("DYNAMIC_SPECIALIST_MAX_CALLS", "4"))
        timeout_seconds = int(os.getenv("DYNAMIC_SPECIALIST_TIMEOUT_SECONDS", "180"))

        def output_from_items(items: List[Any]) -> str:
            text = ItemHelpers.text_message_outputs(items).strip()
            tool_outputs: List[str] = []
            for item in items:
                try:
                    payload = item.to_input_item()
                except Exception:
                    continue
                if isinstance(payload, dict) and payload.get("type") == "function_call_output":
                    output = str(payload.get("output", ""))
                    if output:
                        tool_outputs.append(output[:4000])
            parts: List[str] = []
            if text:
                parts.append(text)
            if tool_outputs:
                parts.append("Recent tool evidence:\n" + "\n\n---\n\n".join(tool_outputs[-6:]))
            return "\n\n".join(parts) if parts else "Specialist finished without a textual summary."

        @function_tool(
            name_override=tool_name,
            description_override=f"{description} Keep the response concise and evidence-focused.",
        )
        async def run_specialist(context: RunContextWrapper, input: str) -> str:
            self.dynamic_specialist_call_counts[name] = self.dynamic_specialist_call_counts.get(name, 0) + 1
            if self.dynamic_specialist_call_counts[name] > max_calls:
                return (
                    f"{tool_name} call budget exhausted after {max_calls} calls. "
                    "Use already collected evidence, ask a different specialist, or verify/finalize."
                )
            try:
                result = await asyncio.wait_for(
                    Runner.run(
                        starting_agent=agent,
                        input=input,
                        context=context.context,
                        hooks=self.run_hooks,
                        run_config=RunConfig(model_provider=self.agent_model_provider),
                        max_turns=12,
                    ),
                    timeout=timeout_seconds,
                )
            except MaxTurnsExceeded:
                return (
                    "Specialist hit its per-call turn limit before producing a final answer. "
                    "Retry with a narrower subtask or delegate to another specialist."
                )
            except asyncio.TimeoutError:
                return (
                    f"Specialist call exceeded {timeout_seconds}s before producing a final answer. "
                    "Use already collected evidence, narrow the subtask, ask another specialist, or verify/finalize."
                )
            for raw_response in result.raw_responses:
                self.usage.add(raw_response.usage)
                self.stats["agent_llm_requests"] += 1
            sub_tool_calls = sum(1 for item in result.new_items if isinstance(item, ToolCallItem))
            self.stats["tool_calls"] += sub_tool_calls
            return output_from_items(result.new_items)

        return run_specialist

    # 주의: 이전 버전에는 task별 deterministic post-agent repair 함수가
    # 있었으나, 정답에 해당하는 파일 경로/셀 좌표/노트 본문/참조 매핑이 코드에
    # 박혀 있어 사실상 groundtruth를 보고 답을 채워주는 cheating이었다.
    # 공정한 비교를 위해 해당 layer를 완전히 제거하고 기본 agent loop만 사용한다.

    async def setup_agent(self) -> None:
        """6개 공통 agent를 만들고 Orchestrator를 루트 agent로 설정한다."""
        if self.architecture == "multi_dynamic_supervisor":
            await self._setup_dynamic_supervisor()
            return

        self._debug_print(">>Initializing multi-agent loop")

        local_tools = self._build_local_tools()
        specialist_tools = self._without_claim_done(local_tools)

        research_agent = Agent(
            name="Research/Inspection Agent",
            **self._agent_kwargs("research", specialist_tools),
        )
        planning_agent = Agent(
            name="Planning Agent",
            **self._agent_kwargs("planning", specialist_tools),
        )
        action_agent = Agent(
            name="Action/Execution Agent",
            **self._agent_kwargs("action", specialist_tools),
        )
        verification_agent = Agent(
            name="Verification Agent",
            **self._agent_kwargs("verification", specialist_tools),
        )
        memory_agent = Agent(
            name="Memory/Summary Agent",
            **self._agent_kwargs("memory", specialist_tools),
        )

        self.specialist_agents = {
            "research": research_agent,
            "planning": planning_agent,
            "action": action_agent,
            "verification": verification_agent,
            "memory": memory_agent,
        }

        self.agent = Agent(
            name="Orchestrator Agent",
            handoffs=[
                research_agent,
                planning_agent,
                action_agent,
                verification_agent,
                memory_agent,
            ],
            **self._agent_kwargs("orchestrator", local_tools),
        )

        available_tools = await self.agent.get_all_tools()
        for tool in available_tools:
            self.all_tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.params_json_schema,
                    },
                }
            )

    async def _setup_dynamic_supervisor(self) -> None:
        """Create an agents-as-tools dynamic supervisor with task-profile specialists."""
        self._debug_print(">>Initializing dynamic supervisor multi-agent loop")

        roster = self._select_dynamic_specialists()
        self.dynamic_profile = self._profile_selection_payload(roster)
        self._write_profile_selection(self.dynamic_profile)

        specialist_agents: Dict[str, Agent] = {}
        specialist_tools: List[object] = []

        for name, spec in roster.items():
            agent = Agent(
                name=f"{name.replace('_', ' ').title()} Specialist",
                **self._agent_kwargs(
                    spec["prompt"],
                    self._local_tools_for_names(spec["local"]),
                    mcp_servers=self._filtered_mcp_servers(spec["mcp"], deny_mutations=spec["read_only"]),
                ),
            )
            specialist_agents[name] = agent
            specialist_tools.append(self._specialist_as_limited_tool(name, agent, spec["description"]))

        root_local_tools = self._local_tools_for_names(
            [
                "local-claim_done",
                "local-check_context_status",
                "local-manage_context",
                "local-smart_context_truncate",
                "local-search_history",
                "local-view_history_turn",
                "local-browse_history",
                "local-history_stats",
                "local-search_in_turn",
            ]
        )

        self.specialist_agents = specialist_agents
        self.agent = Agent(
            name="Dynamic Supervisor Orchestrator",
            handoffs=[],
            **self._agent_kwargs(
                "dynamic_orchestrator",
                [*specialist_tools, *root_local_tools],
                mcp_servers=[],
            ),
        )

        available_tools = await self.agent.get_all_tools()
        for tool in available_tools:
            self.all_tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.params_json_schema,
                    },
                }
            )
