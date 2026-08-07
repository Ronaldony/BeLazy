"""Runtime port for Director calls.

W03 owns only this interface.  Model selection, network access, retries,
credentials, and receipt persistence belong to a later trusted runtime.
"""

from __future__ import annotations

from typing import Protocol

from video_factory.blueprint import ProductionBlueprint

from .contracts import DirectorAssessment, DirectorTaskPlan


class DirectorRuntimePort(Protocol):
    def assess(
        self, task: DirectorTaskPlan, blueprint: ProductionBlueprint
    ) -> DirectorAssessment: ...


__all__ = ["DirectorRuntimePort"]
