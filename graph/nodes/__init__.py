"""
Graph Nodes Package — Export các node chính và nạp type hints cho pydantic-graph.
"""

from __future__ import annotations

import sys

from graph.nodes.project_init import ProjectInitializerNode
from graph.nodes.input_analyzer import InputAnalyzerNode
from graph.nodes.input_guardrail import InputGateGuardrailNode
from graph.nodes.need_more_info import NeedMoreInfoNode
from graph.nodes.bug_explainer import BugExplainerNode
from graph.nodes.planning_strategy import PlanningStrategyNode
from graph.nodes.direct_fix import DirectFixCreationNode
from graph.nodes.planning import PlanningNode
from graph.nodes.plan_interceptor import PlanInterceptorNode
from graph.nodes.execution import ExecutionNode
from graph.nodes.validation import ValidationNode
from graph.nodes.report import ReportNode

# Nạp các class vào module namespace để pydantic-graph get_type_hints nhận diện
sys.modules["graph.nodes.project_init"].__dict__["InputAnalyzerNode"] = InputAnalyzerNode
sys.modules["graph.nodes.input_analyzer"].__dict__["InputGateGuardrailNode"] = InputGateGuardrailNode
sys.modules["graph.nodes.input_guardrail"].__dict__["NeedMoreInfoNode"] = NeedMoreInfoNode
sys.modules["graph.nodes.input_guardrail"].__dict__["BugExplainerNode"] = BugExplainerNode
sys.modules["graph.nodes.need_more_info"].__dict__["InputAnalyzerNode"] = InputAnalyzerNode
sys.modules["graph.nodes.bug_explainer"].__dict__["PlanningStrategyNode"] = PlanningStrategyNode
sys.modules["graph.nodes.bug_explainer"].__dict__["ReportNode"] = ReportNode
sys.modules["graph.nodes.planning_strategy"].__dict__["DirectFixCreationNode"] = DirectFixCreationNode
sys.modules["graph.nodes.planning_strategy"].__dict__["PlanningNode"] = PlanningNode
sys.modules["graph.nodes.direct_fix"].__dict__["ValidationNode"] = ValidationNode
sys.modules["graph.nodes.planning"].__dict__["PlanInterceptorNode"] = PlanInterceptorNode
sys.modules["graph.nodes.plan_interceptor"].__dict__["ExecutionNode"] = ExecutionNode
sys.modules["graph.nodes.plan_interceptor"].__dict__["PlanningNode"] = PlanningNode
sys.modules["graph.nodes.plan_interceptor"].__dict__["ReportNode"] = ReportNode
sys.modules["graph.nodes.execution"].__dict__["ValidationNode"] = ValidationNode
sys.modules["graph.nodes.execution"].__dict__["PlanningNode"] = PlanningNode
sys.modules["graph.nodes.validation"].__dict__["DirectFixCreationNode"] = DirectFixCreationNode
sys.modules["graph.nodes.validation"].__dict__["PlanningNode"] = PlanningNode
sys.modules["graph.nodes.validation"].__dict__["ReportNode"] = ReportNode


__all__ = [
    "ProjectInitializerNode",
    "InputAnalyzerNode",
    "InputGateGuardrailNode",
    "NeedMoreInfoNode",
    "BugExplainerNode",
    "PlanningStrategyNode",
    "DirectFixCreationNode",
    "PlanningNode",
    "PlanInterceptorNode",
    "ExecutionNode",
    "ValidationNode",
    "ReportNode",
]
