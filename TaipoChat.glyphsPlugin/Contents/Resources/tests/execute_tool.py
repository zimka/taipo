# encoding: utf-8
"""Test-only shim for legacy smoke tests that call execute_tool(name, args, ctx)."""

from tools.model_toolset import ModelToolset


def execute_tool(name, args, ctx):
    return ModelToolset(ctx).execute(name, args)
