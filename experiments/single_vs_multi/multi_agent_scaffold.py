"""Toolathlon용 일반 목적 멀티에이전트 scaffold.

이 모듈은 공식 Toolathlon의 TaskAgent를 최소 침습 방식으로 확장한다.
핵심 실행 루프, MCP 연결, workspace 초기화, 평가 로그 저장은 원본 구현을
그대로 사용하고, Agent 구성을 단일 Assistant에서 공통 6-agent 구조로
바꾸는 데 집중한다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, List

from agents import Agent, ModelSettings

from utils.roles.task_agent import TaskAgent, local_tool_mappings


PROMPT_FILES: Dict[str, str] = {
    "orchestrator": "orchestrator.md",
    "research": "research_inspection.md",
    "planning": "planning.md",
    "action": "action_execution.md",
    "verification": "verification.md",
    "memory": "memory_summary.md",
}


class MultiAgentTaskAgent(TaskAgent):
    """공식 TaskAgent 실행 루프를 재사용하는 orchestrator-worker agent."""

    def __init__(self, *args, prompt_dir: str | Path | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.prompt_dir = Path(prompt_dir) if prompt_dir else Path(__file__).parent / "prompts"
        self.specialist_agents: Dict[str, Agent] = {}

    def _read_prompt(self, prompt_key: str) -> str:
        path = self.prompt_dir / PROMPT_FILES[prompt_key]
        return path.read_text(encoding="utf-8")

    def _compose_prompt(self, prompt_key: str) -> str:
        base_prompt = self._read_prompt(prompt_key).strip()
        task_prompt = self.task_config.system_prompts.agent or ""
        fairness_note = """

공통 실험 제약:
- 같은 Toolathlon task_config, 같은 모델, 같은 도구 집합을 사용한다.
- task-specific agent 유형을 만들지 않는다.
- 평가 스크립트나 정답 상태를 변경하지 않는다.
- 도구 접근은 현재 구현상 동일 객체를 공유하되, 역할별 프롬프트 제한으로 읽기/쓰기 책임을 구분한다.

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

    def _agent_kwargs(self, prompt_key: str, tools: Iterable[object]) -> dict:
        return {
            "instructions": self._compose_prompt(prompt_key),
            "model": self._model(),
            "mcp_servers": [*self.mcp_manager.get_all_connected_servers()],
            "tools": list(tools),
            "hooks": self.agent_hooks,
            "model_settings": self._model_settings(),
        }

    async def setup_agent(self) -> None:
        """6개 공통 agent를 만들고 Orchestrator를 루트 agent로 설정한다."""
        self._debug_print(">>Initializing multi-agent loop")

        local_tools = self._build_local_tools()

        research_agent = Agent(
            name="Research/Inspection Agent",
            **self._agent_kwargs("research", local_tools),
        )
        planning_agent = Agent(
            name="Planning Agent",
            **self._agent_kwargs("planning", local_tools),
        )
        action_agent = Agent(
            name="Action/Execution Agent",
            **self._agent_kwargs("action", local_tools),
        )
        verification_agent = Agent(
            name="Verification Agent",
            **self._agent_kwargs("verification", local_tools),
        )
        memory_agent = Agent(
            name="Memory/Summary Agent",
            **self._agent_kwargs("memory", local_tools),
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
