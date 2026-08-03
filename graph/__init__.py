"""
Graph Module — Khởi tạo pydantic-graph Workflow cho PyFix-Agents v2.
"""

from __future__ import annotations

from pydantic_graph import GraphBuilder

from graph.models import BugFixState
import graph.nodes as nodes

# ── Xây dựng Graph bằng GraphBuilder (pydantic-graph) ───────────────────
_builder = GraphBuilder(
    state_type=BugFixState,
    input_type=nodes.ProjectInitializerNode,
    output_type=str,
    auto_instrument=False,
)

_builder.add_edge(_builder.start_node, nodes.ProjectInitializerNode)

_builder.add(
    _builder.node(nodes.ProjectInitializerNode),
    _builder.node(nodes.InputAnalyzerNode),
    _builder.node(nodes.InputGateGuardrailNode),
    _builder.node(nodes.NeedMoreInfoNode),
    _builder.node(nodes.BugExplainerNode),
    _builder.node(nodes.PlanningStrategyNode),
    _builder.node(nodes.DirectFixCreationNode),
    _builder.node(nodes.PlanningNode),
    _builder.node(nodes.PlanInterceptorNode),
    _builder.node(nodes.ExecutionNode),
    _builder.node(nodes.ValidationNode),
    _builder.node(nodes.ReportNode),
)

bug_fix_graph = _builder.build()

__all__ = ["bug_fix_graph", "BugFixState", "ProjectInitializerNode"]


