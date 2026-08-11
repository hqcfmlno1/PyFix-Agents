"""
Graph Module — Khởi tạo pydantic-graph Workflow cho PyFix-Agents v2.
"""

from __future__ import annotations

from pydantic_graph import GraphBuilder

from graph.models import BugFixState

# Import explicitly to populate local namespace for get_type_hints
from graph.nodes.project_init import ProjectInitializerNode
from graph.nodes.input_analyzer import InputAnalyzerNode
from graph.nodes.input_guardrail import InputGateGuardrailNode
from graph.nodes.need_more_info import NeedMoreInfoNode
from graph.nodes.planning import PlanningNode
from graph.nodes.plan_interceptor import PlanInterceptorNode
from graph.nodes.execution import ExecutionNode
from graph.nodes.validation import ValidationNode
from graph.nodes.report import ReportNode
from graph.nodes.reproduction_plan import ReproductionPlanNode
from graph.nodes.reproduction_run import ReproductionRunNode


# ── Xây dựng Graph bằng GraphBuilder (pydantic-graph) ───────────────────
_builder = GraphBuilder(
    state_type=BugFixState,
    input_type=ProjectInitializerNode,
    output_type=str,
    auto_instrument=False,
)

_builder.add_edge(_builder.start_node, ProjectInitializerNode)

_builder.add(
    _builder.node(ProjectInitializerNode),
    _builder.node(InputAnalyzerNode),
    _builder.node(InputGateGuardrailNode),
    _builder.node(NeedMoreInfoNode),
    _builder.node(PlanningNode),
    _builder.node(PlanInterceptorNode),
    _builder.node(ExecutionNode),
    _builder.node(ValidationNode),
    _builder.node(ReportNode),
    _builder.node(ReproductionPlanNode),
    _builder.node(ReproductionRunNode)
)

bug_fix_graph = _builder.build()

__all__ = ["bug_fix_graph", "BugFixState", "ProjectInitializerNode"]
